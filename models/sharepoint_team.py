import base64
import glob
import hashlib
import json
import mimetypes
import re
import unicodedata
from collections import defaultdict
from datetime import timezone
from html import escape
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dateutil.parser import isoparse
from lxml import html as lxml_html
from lxml.etree import ParserError

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SharePointTeam(models.Model):
    _name = "sharepoint.team"
    _description = "Tím SharePoint"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"
    _GRAPH_BANNER_WEBPART_TYPE = "cbe7b0a9-3504-44dd-a3a3-0e5cacd07788"

    name = fields.Char(string="Názov", required=True, tracking=True)
    sharepoint_source_key = fields.Char(string="Kľúč zdroja SharePoint", copy=False, index=True)
    active = fields.Boolean(string="Aktívne", default=True, tracking=True)
    description = fields.Text(string="Popis")
    company_id = fields.Many2one(
        "res.company",
        string="Spoločnosť",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    color = fields.Integer(string="Farba")
    hr_portal_managed = fields.Boolean(
        string="Spravovaný HR portál",
        copy=False,
        index=True,
        help="Automaticky udržiava aktívnych zamestnancov s aktívnymi internými používateľmi ako návštevníkov.",
    )
    member_ids = fields.One2many(
        "sharepoint.team.member",
        "team_id",
        string="Členovia",
        copy=True,
    )
    owner_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_owner_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Vlastníci",
    )
    admin_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_admin_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Administrátori",
    )
    accessible_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_accessible_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Používatelia s prístupom",
    )
    synced_partner_ids = fields.Many2many(
        "res.partner",
        "sharepoint_team_synced_partner_rel",
        "team_id",
        "partner_id",
        copy=False,
        string="Synchronizovaní partneri",
    )
    document_folder_id = fields.Many2one(
        "documents.document",
        readonly=True,
        copy=False,
        string="Knižnica dokumentov",
    )
    knowledge_article_id = fields.Many2one(
        "knowledge.article",
        readonly=True,
        copy=False,
        string="Tímová stránka",
    )
    document_count = fields.Integer(string="Počet dokumentov", compute="_compute_content_counts")
    page_count = fields.Integer(string="Počet stránok", compute="_compute_content_counts")
    user_role = fields.Selection(
        [("owner", "Vlastník"), ("admin", "Administrátor"), ("member", "Používateľ"), ("visitor", "Návštevník")],
        compute="_compute_user_role",
        string="Moja rola",
    )

    _unique_name_company = models.Constraint(
        "UNIQUE(name, company_id)",
        "Tím SharePoint s týmto názvom už v tejto spoločnosti existuje.",
    )
    _unique_source_key_company = models.Constraint(
        "UNIQUE(sharepoint_source_key, company_id)",
        "Tento zdroj SharePoint už má tím v tejto spoločnosti.",
    )

    @api.depends("member_ids.user_id", "member_ids.role")
    def _compute_access_users(self):
        for team in self:
            active_members = team.member_ids.filtered(lambda member: member.user_id.active)
            team.accessible_user_ids = active_members.user_id
            team.owner_user_ids = active_members.filtered(lambda member: member.role == "owner").user_id
            team.admin_user_ids = active_members.filtered(lambda member: member.role == "admin").user_id

    def _compute_content_counts(self):
        Document = self.env["documents.document"].sudo()
        Article = self.env["knowledge.article"].sudo()
        for team in self:
            team.document_count = (
                Document.search_count([
                    ("id", "child_of", team.document_folder_id.id),
                    ("type", "!=", "folder"),
                ])
                if team.document_folder_id
                else 0
            )
            team.page_count = (
                Article.search_count([
                    ("id", "child_of", team.knowledge_article_id.id),
                    ("active", "=", True),
                ])
                if team.knowledge_article_id
                else 0
            )

    @api.depends("member_ids.user_id", "member_ids.role")
    def _compute_user_role(self):
        role_order = {"visitor": 1, "member": 2, "admin": 3, "owner": 4}
        for team in self:
            user_members = team.member_ids.filtered(lambda member: member.user_id == self.env.user)
            role = False
            for member in user_members:
                if not role or role_order[member.role] > role_order[role]:
                    role = member.role
            team.user_role = role

    @api.constrains("member_ids")
    def _check_has_owner(self):
        for team in self:
            if team.active and not team.member_ids.filtered(lambda member: member.role == "owner"):
                raise ValidationError(_("Tím musí mať aspoň jedného vlastníka."))

    @api.model_create_multi
    def create(self, vals_list):
        normalized_vals_list = []
        for vals in vals_list:
            vals = dict(vals)
            if not vals.get("member_ids") and self.env.user.partner_id:
                vals["member_ids"] = [
                    Command.create({"user_id": self.env.user.id, "role": "owner"})
                ]
            normalized_vals_list.append(vals)
        teams = super().create(normalized_vals_list)
        teams._ensure_team_resources()
        teams._sync_native_access()
        return teams

    def write(self, vals):
        res = super().write(vals)
        if {"name", "company_id"} & set(vals):
            self._sync_resource_metadata()
        if {"member_ids", "active"} & set(vals):
            self._ensure_team_resources()
            self._sync_native_access()
        return res

    def action_archive(self):
        self.write({"active": False})
        return True

    def action_unarchive(self):
        self.write({"active": True})
        return True

    def action_sync_access(self):
        self._ensure_team_resources()
        managed_hr_portals = self.filtered("hr_portal_managed")
        if managed_hr_portals:
            managed_hr_portals._sync_hr_portal_employee_visitors()
            (self - managed_hr_portals)._sync_native_access()
        else:
            self._sync_native_access()
        return True

    def action_open_documents(self):
        self.ensure_one()
        if not self.document_folder_id:
            raise UserError(_("Tento tím ešte nemá knižnicu dokumentov."))
        return {
            "type": "ir.actions.client",
            "tag": "document_action_preference",
            "name": self.document_folder_id.display_name,
            "context": {
                "documents_init_folder_id": self.document_folder_id.id,
                "documents_unique_folder_id": self.document_folder_id.id,
                "searchpanel_default_user_folder_id": self.document_folder_id.id,
                "default_folder_id": self.document_folder_id.id,
                "default_user_folder_id": str(self.document_folder_id.id),
            },
            "target": "self",
        }

    def action_open_pages(self):
        self.ensure_one()
        if not self.knowledge_article_id:
            raise UserError(_("Tento tím ešte nemá stránku v Znalostiach."))
        return {
            "type": "ir.actions.act_window",
            "name": self.knowledge_article_id.display_name,
            "res_model": "knowledge.article",
            "view_mode": "form",
            "views": [[False, "form"]],
            "res_id": self.knowledge_article_id.id,
            "target": "current",
        }

    def _ensure_team_resources(self):
        for team in self.sudo():
            if not team.document_folder_id:
                team.document_folder_id = self.env["documents.document"].sudo().create(
                    team._prepare_document_folder_vals()
                )
            if not team.knowledge_article_id:
                team.knowledge_article_id = self.env["knowledge.article"].sudo().create(
                    team._prepare_knowledge_article_vals()
                )

    def _prepare_document_folder_vals(self):
        self.ensure_one()
        return {
            "name": self.name,
            "type": "folder",
            "company_id": self.company_id.id,
            "owner_id": False,
            "access_internal": "none",
            "access_via_link": "none",
            "is_access_via_link_hidden": True,
        }

    def _prepare_knowledge_article_vals(self):
        self.ensure_one()
        writer_partner = self._get_team_role_partners().get("owner") or self.env.user.partner_id
        return {
            "name": self.name,
            "body": self._get_default_article_body(),
            "internal_permission": "none",
            "article_member_ids": [
                Command.create({"partner_id": writer_partner[:1].id, "permission": "write"})
            ],
        }

    def _get_default_article_body(self):
        self.ensure_one()
        if self.hr_portal_managed:
            return _(
                "<h2>HR portál</h2>"
                "<p>Interné HR novinky, dokumenty a užitočné informácie pre zamestnancov.</p>"
            )
        return _(
            "<h2>%(team)s</h2><p>Tímové novinky, poznámky, rozhodnutia a užitočné odkazy.</p>",
            team=self.name,
        )

    def _sync_resource_metadata(self):
        for team in self.sudo():
            if team.document_folder_id:
                team.document_folder_id.write({
                    "name": team.name,
                    "company_id": team.company_id.id,
                })
            if team.knowledge_article_id:
                team.knowledge_article_id.write({"name": team.name})

    def _sync_native_access(self):
        for team in self.sudo():
            team._sync_document_access()
            team._sync_knowledge_access()
            team.synced_partner_ids = [
                Command.set(team._union_role_partners(team._get_team_role_partners()).ids)
            ]

    def _get_team_role_partners(self):
        self.ensure_one()
        partners_by_role = defaultdict(lambda: self.env["res.partner"])
        members = self.member_ids.filtered(lambda member: member.user_id and member.user_id.partner_id)
        for member in members:
            partners_by_role[member.role] |= member.user_id.partner_id
        return partners_by_role

    def _sync_document_access(self):
        self.ensure_one()
        if not self.document_folder_id:
            return
        partners_by_role = self._get_team_role_partners()
        role_by_partner = {}
        for role, partners in partners_by_role.items():
            document_role = "view" if role == "visitor" else "edit"
            for partner in partners:
                role_by_partner[partner] = document_role

        partners = {
            partner: (role, False)
            for partner, role in role_by_partner.items()
            if partner.user_ids != self.env.ref("base.user_root")
        }
        current_partners = self._union_role_partners(partners_by_role)
        for partner in self.synced_partner_ids - current_partners:
            partners[partner] = (False, False)

        self.document_folder_id.sudo().action_update_access_rights(
            access_internal="none",
            access_via_link="none",
            is_access_via_link_hidden=True,
            partners=partners,
        )

    def _sync_knowledge_access(self):
        self.ensure_one()
        article = self.knowledge_article_id.sudo()
        if not article:
            return
        partners_by_role = self._get_team_role_partners()
        permission_by_partner = {}
        for role, partners in partners_by_role.items():
            permission = "read" if role == "visitor" else "write"
            for partner in partners:
                permission_by_partner[partner] = permission

        if not any(permission == "write" for permission in permission_by_partner.values()):
            permission_by_partner[self.env.user.partner_id] = "write"

        commands = []
        existing_members = {
            member.partner_id: member
            for member in article.article_member_ids
            if member.partner_id in self.synced_partner_ids or member.partner_id in permission_by_partner
        }
        for partner, permission in permission_by_partner.items():
            member = existing_members.get(partner)
            if member:
                commands.append(Command.update(member.id, {"permission": permission}))
            else:
                commands.append(Command.create({"partner_id": partner.id, "permission": permission}))
        current_partners = self._union_role_partners(partners_by_role)
        for partner in self.synced_partner_ids - current_partners:
            member = existing_members.get(partner)
            if member:
                commands.append(Command.delete(member.id))

        article.with_context(knowledge_member_skip_writable_check=True).write({
            "internal_permission": "none",
            "article_member_ids": commands,
        })

    def _union_role_partners(self, partners_by_role):
        partners = self.env["res.partner"]
        for role_partners in partners_by_role.values():
            partners |= role_partners
        return partners

    @api.model
    def _get_existing_hr_portal_team(self):
        xmlid = "sharepoint.sharepoint_team_hr_portal"
        team = self.env.ref(xmlid, raise_if_not_found=False)
        if team:
            return team.sudo()
        Team = self.sudo().with_context(active_test=False)
        team = Team.search([("hr_portal_managed", "=", True)], limit=1)
        if team:
            return team
        return Team.search([
            ("name", "=", "HR portál"),
            ("company_id", "=", self.env.company.id),
        ], limit=1)

    @api.model
    def _get_or_create_hr_portal_team(self):
        xmlid = "sharepoint.sharepoint_team_hr_portal"
        team = self.env.ref(xmlid, raise_if_not_found=False)
        if team:
            if not team.hr_portal_managed:
                team.sudo().write({"hr_portal_managed": True})
            team = team.sudo()
            team._ensure_hr_portal_background_folder()
            team._sync_hr_portal_background_documents()
            return team

        Team = self.sudo().with_context(active_test=False)
        team = Team.search([("hr_portal_managed", "=", True)], limit=1)
        if not team:
            team = Team.search([
                ("name", "=", "HR portál"),
                ("company_id", "=", self.env.company.id),
            ], limit=1)
        if not team:
            owner = self.env.ref("base.user_admin", raise_if_not_found=False) or self.env.user
            team = Team.create({
                "name": "HR portál",
                "description": _(
                    "Zamestnanecký HR portál pre interné oznamy, stránky v Znalostiach a HR súbory."
                ),
                "company_id": self.env.company.id,
                "hr_portal_managed": True,
                "member_ids": [
                    Command.create({"user_id": owner.id, "role": "owner", "source": "manual"})
                ],
            })
        elif not team.hr_portal_managed:
            team.write({"hr_portal_managed": True})

        self.env["ir.model.data"].sudo()._update_xmlids([{
            "xml_id": xmlid,
            "record": team,
            "noupdate": True,
        }], update=True)
        team._ensure_hr_portal_background_folder()
        team._sync_hr_portal_background_documents()
        return team

    def _ensure_hr_portal_background_folder(self):
        self.ensure_one()
        if not self.hr_portal_managed:
            return self.env["documents.document"]
        self._ensure_team_resources()
        Document = self.env["documents.document"].sudo()
        folder = Document.search([
            ("type", "=", "folder"),
            ("folder_id", "=", self.document_folder_id.id),
            ("name", "=", "Pozadia blogov"),
        ], limit=1)
        if not folder:
            folder = Document.create({
                "name": "Pozadia blogov",
                "type": "folder",
                "folder_id": self.document_folder_id.id,
                "company_id": self.company_id.id,
                "owner_id": False,
                "access_internal": "none",
                "access_via_link": "none",
                "is_access_via_link_hidden": True,
            })
            self._sync_document_access()
        return folder

    def _sync_hr_portal_background_documents(self):
        for team in self.sudo().filtered("hr_portal_managed"):
            if not team.document_folder_id:
                continue
            background_folder = team._ensure_hr_portal_background_folder()
            documents = self.env["documents.document"].sudo().search([
                ("type", "!=", "folder"),
                ("folder_id", "=", team.document_folder_id.id),
            ])
            jpg_documents = documents.filtered(
                lambda document: (
                    team._is_hr_portal_background_document_name(document.name)
                    or document.mimetype == "image/jpeg"
                )
            )
            if jpg_documents:
                jpg_documents.write({"folder_id": background_folder.id})

    def _get_hr_portal_import_folder(self, filename):
        self.ensure_one()
        if self.hr_portal_managed and self._is_hr_portal_background_document_name(filename):
            return self._ensure_hr_portal_background_folder()
        return self.document_folder_id

    def _is_hr_portal_background_document_name(self, filename):
        return bool(filename and filename.lower().endswith((".jpg", ".jpeg")))

    @api.model
    def _get_hr_portal_employee_users(self):
        employees = self.env["hr.employee"].sudo().with_context(active_test=False).search([
            ("active", "=", True),
            ("user_id", "!=", False),
            ("user_id.active", "=", True),
            ("user_id.share", "=", False),
        ])
        return employees.user_id

    @api.model
    def _cron_sync_hr_portal_employee_visitors(self):
        return self._sync_hr_portal_employee_visitors_all()

    @api.model
    def _sync_hr_portal_employee_visitors_all(self):
        team = self._get_or_create_hr_portal_team()
        return team._sync_hr_portal_employee_visitors()

    def _sync_hr_portal_employee_visitors(self):
        total_members = 0
        for team in self.sudo():
            team._ensure_team_resources()
            target_users = self._get_hr_portal_employee_users()
            target_user_ids = set(target_users.ids)
            existing_by_user_id = {
                member.user_id.id: member
                for member in team.member_ids
                if member.user_id
            }
            commands = []
            for user in target_users:
                member = existing_by_user_id.get(user.id)
                if member:
                    if member.source == "hr_employee" and member.role != "visitor":
                        commands.append(Command.update(member.id, {"role": "visitor"}))
                    continue
                commands.append(Command.create({
                    "user_id": user.id,
                    "role": "visitor",
                    "source": "hr_employee",
                }))
            for member in team.member_ids.filtered(lambda rec: rec.source == "hr_employee"):
                if member.user_id.id not in target_user_ids:
                    commands.append(Command.delete(member.id))
            if commands:
                team.with_context(sharepoint_hr_portal_sync=True).write({"member_ids": commands})
            else:
                team._sync_native_access()
            total_members += len(target_users)
        return total_members

    def action_import_hr_portal_graph_pages(self, export_path, media_dir=False):
        self.ensure_one()
        if not self.hr_portal_managed:
            raise UserError(_("Import stránok SharePoint je dostupný iba pre spravovaný tím HR portálu."))
        self._sync_hr_portal_employee_visitors()
        return self.import_graph_site_pages(export_path, media_dir=media_dir)

    @api.model
    def import_hr_portal_graph_pages(self, export_path, media_dir=False):
        team = self._get_or_create_hr_portal_team()
        team._sync_hr_portal_employee_visitors()
        return team.import_graph_site_pages(export_path, media_dir=media_dir)

    def import_graph_site_pages(self, export_path, media_dir=False):
        """Import a saved Microsoft Graph site export into this managed team."""
        self.ensure_one()
        return self._import_graph_site_pages(export_path, media_dir=media_dir)

    def _import_hr_portal_graph_pages(self, export_path, media_dir=False):
        """Compatibility wrapper for callers of the original HR-specific API."""
        self.ensure_one()
        return self._import_graph_site_pages(export_path, media_dir=media_dir)

    def _import_graph_site_pages(self, export_path, media_dir=False):
        self.ensure_one()
        self._ensure_team_resources()
        export_file = Path(export_path).expanduser()
        if not export_file.exists():
            raise UserError(_("Exportný súbor SharePoint sa nenašiel: %s", export_path))
        with export_file.open(encoding="utf-8") as handle:
            payload = json.load(handle)

        pages = payload.get("value", payload) if isinstance(payload, dict) else payload
        if not isinstance(pages, list):
            raise UserError(_("Export SharePoint musí byť JSON zoznam alebo objekt so zoznamom „value“."))

        stats = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "documents_created": 0,
            "documents_updated": 0,
            "documents_unchanged": 0,
            "documents_placeholders": 0,
            "documents_stale": 0,
            "stale_documents": [],
        }
        media_path = Path(media_dir).expanduser() if media_dir else False
        drive_items = self._graph_payload_drive_items(payload)
        Article = self.env["knowledge.article"].sudo()
        for page in pages:
            if not isinstance(page, dict):
                stats["skipped"] += 1
                continue
            source_id = page.get("id") or page.get("webUrl") or page.get("name")
            title = page.get("title") or page.get("name")
            if not source_id or not title:
                stats["skipped"] += 1
                continue
            article = Article.with_context(active_test=False).search([
                ("sharepoint_source_id", "=", source_id),
                ("parent_id", "=", self.knowledge_article_id.id),
            ], limit=1)
            document_context = self._prepare_graph_page_document_context(
                page,
                drive_items,
                media_path,
                stats,
            )
            if not self._graph_page_has_importable_content(page):
                if article and article.active:
                    article.action_send_to_trash()
                stats["skipped"] += 1
                continue
            body = self._graph_page_to_knowledge_body(page, media_path, document_context)
            cover_vals = self._graph_page_cover_vals(page, media_path)
            vals = {
                "name": title,
                "body": body,
                "parent_id": self.knowledge_article_id.id,
                "internal_permission": False,
                "is_desynchronized": False,
                "sharepoint_source_id": source_id,
                "sharepoint_source_url": page.get("webUrl"),
                "sharepoint_source_modified": self._graph_datetime(page.get("lastModifiedDateTime")),
                "sharepoint_source_author": self._graph_page_author(page),
            }
            vals.update(cover_vals)
            if article:
                if not article.active:
                    article.action_unarchive()
                article.with_context(knowledge_member_skip_writable_check=True).write(vals)
                stats["updated"] += 1
            else:
                Article.with_context(knowledge_member_skip_writable_check=True).create(vals)
                stats["created"] += 1
        return stats

    def _graph_payload_drive_items(self, payload):
        if not isinstance(payload, dict):
            return []
        for key in ("driveItems", "drive_items", "items"):
            items = payload.get(key)
            if isinstance(items, dict):
                items = items.get("value")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        return []

    def _graph_page_has_importable_content(self, page):
        if page.get("description"):
            return True
        return any(
            webpart
            for section in self._iter_graph_sections(page)
            for column in section.get("columns") or []
            for webpart in column.get("webparts") or []
            if isinstance(webpart, dict)
        )

    def _graph_page_to_knowledge_body(self, page, media_path=False, import_context=False):
        title = escape(page.get("title") or page.get("name") or _("Bez názvu"))
        parts = [f"<h1>{title}</h1>"]
        metadata = self._graph_page_metadata_html(page)
        if metadata:
            parts.append(metadata)

        page_parts = []
        unsupported = []
        for section in self._iter_graph_sections(page):
            rendered = self._render_graph_section(section, page, media_path, unsupported, import_context)
            if rendered:
                page_parts.append(rendered)

        if page_parts:
            parts.extend(page_parts)
        elif page.get("description"):
            parts.append(f"<p>{escape(page['description'])}</p>")
        else:
            parts.append("<p></p>")

        if unsupported:
            parts.append(
                "<blockquote><p>%s</p><ul>%s</ul></blockquote>"
                % (
                    escape(_("Niektoré bloky SharePoint sa nepodarilo automaticky skonvertovať.")),
                    "".join(f"<li>{escape(str(item))}</li>" for item in unsupported),
                )
            )
        if page.get("webUrl"):
            parts.append(
                '<p><a href="%s" target="_blank" rel="noopener">%s</a></p>'
                % (escape(page["webUrl"], quote=True), escape(_("Otvoriť pôvodnú stránku SharePoint")))
            )
        return self._normalize_knowledge_body_html("\n".join(parts), page, import_context=import_context)

    def _normalize_knowledge_body_html(
        self,
        body,
        page=False,
        compact_styles=False,
        import_context=False,
    ):
        if not body:
            return body
        try:
            root = lxml_html.fragment_fromstring(body, create_parent="div")
        except (ParserError, TypeError, ValueError):
            return body

        # Odoo's caption plugin treats every <figure> as an image figure. SharePoint
        # exports tables with <figure class="table">, so keep the contents but use
        # a neutral block tag for non-image figures.
        for figure in root.xpath(".//figure[not(.//img)]"):
            figure.tag = "div"
            figure.set("class", "o_sharepoint_import_table table-responsive mb-3")
            for table in figure.xpath(".//table"):
                table_classes = set((table.get("class") or "").split())
                table_classes.update(["table", "table-sm"])
                table.set("class", " ".join(sorted(table_classes)))

        if compact_styles:
            for node in root.xpath(".//*[@style]"):
                style = self._compact_graph_column_style(node.get("style"))
                if style:
                    node.set("style", style)
                else:
                    node.attrib.pop("style", None)

        for node, attribute in (
            *((anchor, "href") for anchor in root.xpath(".//a[@href]")),
            *((image, "src") for image in root.xpath(".//img[@src]")),
        ):
            absolute_url = self._graph_absolute_url(node.get(attribute), page)
            if attribute == "href":
                absolute_url = self._graph_imported_document_url(absolute_url, import_context) or absolute_url
            node.set(attribute, absolute_url)

        return (root.text or "") + "".join(
            lxml_html.tostring(child, encoding="unicode", method="html")
            for child in root
        )

    def _compact_graph_column_style(self, style):
        properties = []
        for declaration in (style or "").split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            name = name.strip().lower()
            value = value.strip()
            if not name or not value:
                continue
            if name in ("margin-left", "padding-left", "text-indent", "text-align-last", "text-justify"):
                continue
            if name == "text-align" and value.lower() == "justify":
                continue
            properties.append(f"{name}: {value}")
        return "; ".join(properties)

    def _graph_page_metadata_html(self, page):
        items = []
        author = self._graph_page_author(page)
        if author:
            items.append(escape(author))
        date = page.get("lastModifiedDateTime") or page.get("createdDateTime")
        if date:
            items.append(escape(str(date)))
        if not items:
            return False
        return '<p class="text-muted">%s</p>' % " · ".join(items)

    def _graph_page_author(self, page):
        for key in ("createdBy", "lastModifiedBy"):
            user = (page.get(key) or {}).get("user") or {}
            value = user.get("displayName") or user.get("email")
            if value:
                return value
        return False

    def _iter_graph_sections(self, page):
        layout = page.get("canvasLayout") or {}
        for section in layout.get("horizontalSections") or []:
            if isinstance(section, dict):
                yield section
        vertical_webparts = layout.get("verticalSection", {}).get("webparts") or []
        if vertical_webparts:
            yield {
                "layout": "oneColumn",
                "emphasis": "none",
                "columns": [{"id": "vertical", "width": 12, "webparts": vertical_webparts}],
            }

    def _render_graph_section(
        self,
        section,
        page,
        media_path=False,
        unsupported=None,
        import_context=False,
    ):
        columns = [column for column in section.get("columns") or [] if column.get("webparts")]
        if not columns:
            return False

        rendered_columns = []
        is_split_section = len(columns) > 1
        for column in columns:
            column_parts = []
            is_narrow_column = is_split_section or int(column.get("width") or 12) < 12
            for webpart in column.get("webparts") or []:
                if not isinstance(webpart, dict):
                    continue
                if self._is_graph_banner_webpart(webpart):
                    continue
                rendered = self._render_graph_webpart(
                    webpart,
                    page,
                    media_path,
                    compact_styles=is_narrow_column,
                    import_context=import_context,
                )
                if rendered:
                    column_parts.append(rendered)
                elif unsupported is not None:
                    unsupported.append(
                        webpart.get("webPartType")
                        or webpart.get("@odata.type")
                        or _("Nepodporovaný webpart")
                    )
            if column_parts:
                rendered_columns.append((column.get("width") or 12, "\n".join(column_parts)))

        if not rendered_columns:
            return False
        if len(rendered_columns) == 1:
            return self._wrap_graph_section(rendered_columns[0][1], section)

        columns_html = []
        for width, content in rendered_columns:
            md_width = max(1, min(12, int(width or 12)))
            columns_html.append(
                '<div class="col-md-%s mb-3" style="min-width: 0; overflow-wrap: break-word;">%s</div>'
                % (md_width, content)
            )
        return self._wrap_graph_section(
            '<div class="row g-3">%s</div>' % "".join(columns_html),
            section,
        )

    def _wrap_graph_section(self, content, section):
        emphasis = section.get("emphasis")
        if emphasis == "soft":
            return '<section class="bg-light p-3 mb-4 rounded">%s</section>' % content
        if emphasis == "strong":
            return '<section class="bg-200 p-3 mb-4 rounded">%s</section>' % content
        return '<section class="mb-4">%s</section>' % content

    def _render_graph_webpart(
        self,
        webpart,
        page,
        media_path=False,
        compact_styles=False,
        import_context=False,
    ):
        odata_type = str(webpart.get("@odata.type") or webpart.get("odataType") or "")
        if "textWebPart" in odata_type or webpart.get("innerHtml"):
            return self._normalize_knowledge_body_html(
                webpart.get("innerHtml") or "",
                page,
                compact_styles=compact_styles,
                import_context=import_context,
            )
        return self._render_graph_standard_webpart(webpart, page, media_path, import_context)

    def _render_graph_standard_webpart(
        self,
        webpart,
        page=False,
        media_path=False,
        import_context=False,
    ):
        webpart_type = webpart.get("webPartType")
        if self._is_graph_banner_webpart(webpart):
            return False
        if webpart_type == "c4bd7b2f-7b6e-4599-8485-16504575f590":
            return self._render_graph_hero_webpart(webpart, page, media_path)
        if webpart_type == "d1d91016-032f-456d-98a4-721247c305e8":
            return self._render_graph_image_webpart(webpart, page, media_path)
        if webpart_type == "c70391ea-0b10-4ee9-b2b4-006d3fcad0cd":
            return self._render_graph_quick_links_webpart(webpart, page, media_path)
        if webpart_type == "f92bf067-bc19-489e-a556-7fe95f508720":
            return self._render_graph_document_library_webpart(webpart, page, import_context)

        strings = list(self._iter_graph_strings(webpart))
        image_urls = [value for value in strings if self._looks_like_file_url(value, image=True)]
        file_urls = [
            value
            for value in strings
            if self._looks_like_file_url(value, image=False)
            and not self._looks_like_file_url(value, image=True)
        ]
        links = []
        for url in image_urls + file_urls:
            url = self._graph_absolute_url(url, page)
            document = self._graph_imported_document(url, import_context)
            if not document:
                document = self._get_or_create_imported_document(url, media_path, page=page)
            if document:
                links.append((document.access_url, document.name))
            elif url.startswith(("http://", "https://")):
                links.append((url, Path(url.split("?", 1)[0]).name or url))
        title = ((webpart.get("data") or {}).get("title") or webpart.get("title") or _("Obsah SharePoint"))
        if not links:
            return False
        return "<section><h3>%s</h3><ul>%s</ul></section>" % (
            escape(str(title)),
            "".join(
                '<li><a href="%s" target="_blank" rel="noopener">%s</a></li>'
                % (escape(url, quote=True), escape(name))
                for url, name in links
            ),
        )

    def _render_graph_hero_webpart(self, webpart, page=False, media_path=False):
        data = webpart.get("data") or {}
        properties = data.get("properties") or {}
        content = properties.get("content") or []
        text_by_key = self._graph_processed_values(data, "searchablePlainTexts")
        link_by_key = self._graph_processed_values(data, "links")
        image_by_key = self._graph_processed_values(data, "imageSources")
        cards = []
        for index, item in enumerate(content):
            title = (
                text_by_key.get(f"content[{index}].title")
                or self._html_to_text(item.get("titleHTML"))
                or item.get("title")
            )
            if not title:
                continue
            link = self._graph_absolute_url(link_by_key.get(f"content[{index}].link") or item.get("url"), page)
            image = self._graph_absolute_url(
                image_by_key.get(f"content[{index}].image.url")
                or (item.get("image") or {}).get("resolvedUrl")
                or (item.get("image") or {}).get("imageUrl"),
                page,
            )
            image = self._get_or_create_imported_image_url(image, media_path) or image
            image_html = (
                '<img class="img-fluid w-100 rounded mb-2" src="%s" alt="%s" loading="lazy"/>'
                % (escape(image, quote=True), escape(title, quote=True))
                if image
                else ""
            )
            card = '<div class="card h-100"><a href="%s" target="_blank" rel="noopener">%s<h3>%s</h3></a></div>' % (
                escape(link or "#", quote=True),
                image_html,
                escape(title),
            )
            cards.append('<div class="col-md-6 mb-3">%s</div>' % card)
        if not cards:
            return False
        return '<div class="row g-3">%s</div>' % "".join(cards)

    def _render_graph_image_webpart(self, webpart, page=False, media_path=False):
        data = webpart.get("data") or {}
        properties = data.get("properties") or {}
        image_by_key = self._graph_processed_values(data, "imageSources")
        src = self._graph_absolute_url(
            image_by_key.get("imageSource")
            or properties.get("imageSource")
            or properties.get("imageUrl"),
            page,
        )
        if not src:
            return False
        src = self._get_or_create_imported_image_url(src, media_path) or src
        alt = properties.get("altText") or properties.get("captionText") or properties.get("fileName") or ""
        caption = properties.get("captionText")
        caption_html = '<p class="text-muted small">%s</p>' % escape(caption) if caption else ""
        return (
            '<div class="mb-2"><img class="img-fluid w-100 rounded" src="%s" alt="%s" loading="lazy"/>%s</div>'
            % (escape(src, quote=True), escape(alt, quote=True), caption_html)
        )

    def _render_graph_quick_links_webpart(self, webpart, page=False, media_path=False):
        data = webpart.get("data") or {}
        properties = data.get("properties") or {}
        text_by_key = self._graph_processed_values(data, "searchablePlainTexts")
        link_by_key = self._graph_processed_values(data, "links")
        image_by_key = self._graph_processed_values(data, "imageSources")
        title = text_by_key.get("title") or self._html_to_text(properties.get("titleHTML"))
        items = properties.get("items") or []
        cards = []
        for index, item in enumerate(items):
            item_title = (
                text_by_key.get(f"items[{index}].title")
                or item.get("title")
                or item.get("Názov")
            )
            item_link = self._graph_absolute_url(
                link_by_key.get(f"items[{index}].sourceItem.url")
                or (item.get("sourceItem") or {}).get("url"),
                page,
            )
            if not item_title or item_title == "Add your own title" or not item_link:
                continue
            description = text_by_key.get(f"items[{index}].description") or item.get("description")
            image = self._graph_absolute_url(
                image_by_key.get(f"items[{index}].image.url")
                or (item.get("image") or {}).get("url"),
                page,
            )
            image = self._get_or_create_imported_image_url(image, media_path) or image
            image_html = (
                '<img class="img-fluid rounded mb-2" src="%s" alt="%s" loading="lazy"/>'
                % (escape(image, quote=True), escape(item_title, quote=True))
                if image and self._looks_like_file_url(image, image=True)
                else ""
            )
            description_html = '<p class="mb-0 text-muted">%s</p>' % escape(description) if description else ""
            cards.append(
                '<div class="col-md-4 mb-3"><a class="card h-100 text-decoration-none p-3" '
                'href="%s" target="_blank" rel="noopener">%s<strong>%s</strong>%s</a></div>'
                % (escape(item_link, quote=True), image_html, escape(item_title), description_html)
            )
        if not cards:
            return False
        title_html = "<h2>%s</h2>" % escape(title) if title else ""
        return '%s<div class="row g-3">%s</div>' % (title_html, "".join(cards))

    def _render_graph_document_library_webpart(self, webpart, page=False, import_context=False):
        data = webpart.get("data") or {}
        properties = data.get("properties") or {}
        text_by_key = self._graph_processed_values(data, "searchablePlainTexts")
        title = text_by_key.get("listTitle") or data.get("title") or _("Dokumenty")
        page_folder = import_context and import_context.get("page_folder")
        if page_folder:
            return (
                '<p><a class="btn btn-secondary" href="%s" target="_blank" rel="noopener">%s</a></p>'
                % (escape(page_folder.access_url or "#", quote=True), escape(title))
            )
        url = self._graph_absolute_url(properties.get("selectedListUrl"), page)
        if not url:
            return False
        return (
            '<p><a class="btn btn-secondary" href="%s" target="_blank" rel="noopener">%s</a></p>'
            % (escape(url, quote=True), escape(title))
        )

    def _is_graph_banner_webpart(self, webpart):
        return webpart.get("webPartType") == self._GRAPH_BANNER_WEBPART_TYPE

    def _graph_page_cover_vals(self, page, media_path=False):
        banner = next(
            (webpart for webpart in self._iter_graph_page_webparts(page) if self._is_graph_banner_webpart(webpart)),
            False,
        )
        if not banner:
            return {}
        data = banner.get("data") or {}
        properties = data.get("properties") or {}
        image_by_key = self._graph_processed_values(data, "imageSources")
        src = self._graph_absolute_url(
            image_by_key.get("imageSource")
            or properties.get("imageSource")
            or properties.get("imageUrl"),
            page,
        )
        cover = self._get_or_create_knowledge_cover(src, media_path)
        if not cover:
            return {}
        vals = {
            "cover_image_id": cover.id,
            "cover_image_position": self._graph_cover_position(properties.get("translateY")),
        }
        return vals

    def _iter_graph_page_webparts(self, page):
        for section in self._iter_graph_sections(page):
            for column in section.get("columns") or []:
                for webpart in column.get("webparts") or []:
                    if isinstance(webpart, dict):
                        yield webpart

    def _graph_cover_position(self, value):
        try:
            position = float(value)
        except (TypeError, ValueError):
            return 50.0
        return max(0.01, min(100.0, position))

    def _graph_processed_values(self, data, collection):
        return {
            item.get("key"): item.get("value")
            for item in (data.get("serverProcessedContent") or {}).get(collection) or []
            if item.get("key") and item.get("value")
        }

    def _html_to_text(self, value):
        if not value:
            return False
        try:
            return lxml_html.fragment_fromstring(value, create_parent="div").text_content().strip()
        except (ParserError, TypeError, ValueError):
            return str(value)

    def _graph_absolute_url(self, value, page=False):
        if not value or not isinstance(value, str):
            return False
        if value.startswith(("http://", "https://", "mailto:", "tel:", "#")):
            return value
        if value.startswith(("/web/", "/document/")):
            return value
        if value.startswith("/"):
            origin = "https://tenenetsk.sharepoint.com"
            page_url = isinstance(page, dict) and page.get("webUrl")
            if page_url:
                parsed = urlsplit(page_url)
                if parsed.scheme and parsed.netloc:
                    origin = f"{parsed.scheme}://{parsed.netloc}"
            return origin + value
        return value

    def _iter_graph_strings(self, value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from self._iter_graph_strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._iter_graph_strings(child)

    def _looks_like_file_url(self, value, image=False):
        if not value or not isinstance(value, str):
            return False
        clean = value.split("?", 1)[0].lower()
        image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
        file_extensions = image_extensions + (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
        return clean.endswith(image_extensions if image else file_extensions)

    def _is_graph_image_filename(self, filename):
        return bool(
            filename
            and str(filename).lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"))
        )

    def _prepare_graph_page_document_context(self, page, drive_items, media_path=False, stats=None):
        context = {
            "document_by_key": {},
            "document_by_identity": {},
            "imported_identity_keys": set(),
            "page_folder": self.env["documents.document"],
        }
        if not media_path:
            return context

        entries = self._graph_page_document_entries(page, drive_items)
        for entry in entries:
            document = self._get_or_create_imported_document(
                entry.get("url"),
                media_path,
                page=page,
                source_item=entry.get("item"),
                stats=stats,
            )
            if not document:
                if stats is not None and (entry.get("item") or {}).get("downloadSkipped"):
                    stats["documents_placeholders"] += 1
                continue
            context["page_folder"] = document.folder_id
            identity_key = self._graph_drive_item_identity_key(entry.get("item"))
            if identity_key:
                context["document_by_identity"][identity_key] = document
                context["imported_identity_keys"].add(identity_key)
            for url in self._graph_entry_urls(entry):
                key = self._graph_url_key(url, page)
                if key:
                    context["document_by_key"][key] = document

        if context["page_folder"]:
            self._mark_graph_page_stale_documents(page, context, stats)
        return context

    def _graph_page_document_entries(self, page, drive_items):
        entries = []
        seen = set()
        item_by_url = self._graph_drive_items_by_url(drive_items)

        def add_entry(url=False, item=False, reason=False):
            if item and self._graph_drive_item_is_folder(item):
                return
            filename = self._graph_drive_item_filename(item) if item else self._local_media_filename(url)
            if self._is_graph_image_filename(filename):
                return
            key = self._graph_drive_item_identity_key(item) or self._graph_url_key(url, page)
            if not key or key in seen:
                return
            seen.add(key)
            entries.append({"url": url or self._graph_drive_item_url(item), "item": item, "reason": reason})

        for url in self._graph_page_explicit_file_urls(page):
            item = item_by_url.get(self._graph_url_key(url, page))
            add_entry(url=url, item=item, reason="explicit")

        for library_url in self._graph_page_document_library_urls(page):
            for item in self._graph_drive_items_under_url(drive_items, library_url, page):
                add_entry(url=self._graph_drive_item_url(item), item=item, reason="document_library")

        for item in self._graph_drive_items_matching_page_folder(drive_items, page):
            add_entry(url=self._graph_drive_item_url(item), item=item, reason="page_folder")
        return entries

    def _graph_page_explicit_file_urls(self, page):
        urls = []
        for webpart in self._iter_graph_page_webparts(page):
            if self._is_graph_banner_webpart(webpart):
                continue
            urls.extend(self._iter_graph_html_file_urls(webpart.get("innerHtml"), page))
            for value in self._iter_graph_strings(webpart):
                if (
                    self._looks_like_file_url(value, image=False)
                    and not self._looks_like_file_url(value, image=True)
                ):
                    urls.append(self._graph_absolute_url(value, page))
        return urls

    def _iter_graph_html_file_urls(self, value, page):
        if not value:
            return []
        try:
            root = lxml_html.fragment_fromstring(value, create_parent="div")
        except (ParserError, TypeError, ValueError):
            return []
        urls = []
        for node, attribute in (
            *((anchor, "href") for anchor in root.xpath(".//a[@href]")),
            *((image, "src") for image in root.xpath(".//img[@src]")),
        ):
            url = self._graph_absolute_url(node.get(attribute), page)
            if self._looks_like_file_url(url, image=False) and not self._looks_like_file_url(url, image=True):
                urls.append(url)
        return urls

    def _graph_page_document_library_urls(self, page):
        urls = []
        for webpart in self._iter_graph_page_webparts(page):
            if webpart.get("webPartType") != "f92bf067-bc19-489e-a556-7fe95f508720":
                continue
            properties = ((webpart.get("data") or {}).get("properties") or {})
            url = self._graph_absolute_url(properties.get("selectedListUrl"), page)
            if url:
                urls.append(url)
        return urls

    def _graph_drive_items_by_url(self, drive_items):
        item_by_url = {}
        for item in drive_items:
            if self._graph_drive_item_is_folder(item):
                continue
            for url in self._graph_drive_item_urls(item):
                key = self._graph_url_key(url)
                if key:
                    item_by_url[key] = item
        return item_by_url

    def _graph_drive_items_under_url(self, drive_items, library_url, page=False):
        library_key = self._graph_url_key(library_url, page)
        library_path = self._graph_path_key(library_url, page)
        for item in drive_items:
            if self._graph_drive_item_is_folder(item):
                continue
            item_paths = [
                self._graph_path_key(url)
                for url in self._graph_drive_item_urls(item)
                if self._graph_path_key(url)
            ]
            item_paths.append(self._graph_path_key(self._graph_drive_item_path(item)))
            if any(path and library_path and path.startswith(library_path.rstrip("/") + "/") for path in item_paths):
                yield item
                continue
            if library_key and any(
                self._graph_url_key(url) and self._graph_url_key(url).startswith(library_key.rstrip("/") + "/")
                for url in self._graph_drive_item_urls(item)
            ):
                yield item

    def _graph_drive_items_matching_page_folder(self, drive_items, page):
        page_names = {
            self._normalize_sharepoint_name(page.get("title")),
            self._normalize_sharepoint_name(Path(page.get("name") or "").stem),
            self._normalize_sharepoint_name(self._graph_page_slug(page)),
        }
        page_names.discard("")
        if not page_names:
            return []
        matches = []
        for item in drive_items:
            if self._graph_drive_item_is_folder(item):
                continue
            filename = self._graph_drive_item_filename(item)
            if self._is_graph_image_filename(filename):
                continue
            path_parts = self._graph_drive_item_path_parts(item)[:-1]
            normalized_parts = {self._normalize_sharepoint_name(part) for part in path_parts}
            if page_names & normalized_parts:
                matches.append(item)
        return matches

    def _mark_graph_page_stale_documents(self, page, context, stats=None):
        if stats is None:
            return
        source_id = page.get("id") or page.get("webUrl") or page.get("name")
        if not source_id:
            return
        stale_documents = self.env["documents.document"].sudo().search([
            ("type", "!=", "folder"),
            ("sharepoint_source_page_id", "=", source_id),
            ("sharepoint_drive_id", "!=", False),
            ("sharepoint_drive_item_id", "!=", False),
        ]).filtered(
            lambda document: (
                document.sharepoint_drive_id,
                document.sharepoint_drive_item_id,
            ) not in context["imported_identity_keys"]
        )
        if not stale_documents:
            return
        stats["documents_stale"] += len(stale_documents)
        stats["stale_documents"].extend(stale_documents.mapped("name"))

    def _graph_imported_document(self, url, import_context=False):
        if not url or not import_context:
            return self.env["documents.document"]
        return import_context.get("document_by_key", {}).get(self._graph_url_key(url)) or self.env["documents.document"]

    def _graph_imported_document_url(self, url, import_context=False):
        document = self._graph_imported_document(url, import_context)
        return document.access_url if document else False

    def _graph_entry_urls(self, entry):
        urls = []
        if entry.get("url"):
            urls.append(entry["url"])
        if entry.get("item"):
            urls.extend(self._graph_drive_item_urls(entry["item"]))
        return urls

    def _graph_drive_item_urls(self, item):
        if not isinstance(item, dict):
            return []
        return [
            url
            for url in (
                item.get("webUrl"),
                item.get("webDavUrl"),
                item.get("@microsoft.graph.downloadUrl"),
                item.get("sourceUrl"),
            )
            if url
        ]

    def _graph_drive_item_url(self, item):
        urls = self._graph_drive_item_urls(item)
        return urls[0] if urls else False

    def _graph_drive_item_filename(self, item):
        if not isinstance(item, dict):
            return False
        return item.get("name") or self._local_media_filename(self._graph_drive_item_url(item))

    def _graph_drive_item_is_folder(self, item):
        return bool(isinstance(item, dict) and item.get("folder"))

    def _graph_drive_item_identity(self, item):
        if not isinstance(item, dict):
            return (False, False)
        parent_reference = item.get("parentReference") or {}
        return (
            item.get("driveId") or parent_reference.get("driveId"),
            item.get("id"),
        )

    def _graph_drive_item_identity_key(self, item):
        drive_id, item_id = self._graph_drive_item_identity(item)
        return (drive_id, item_id) if drive_id and item_id else False

    def _graph_drive_item_modified(self, item):
        if not isinstance(item, dict):
            return False
        return self._graph_datetime(
            item.get("lastModifiedDateTime")
            or (item.get("fileSystemInfo") or {}).get("lastModifiedDateTime")
        )

    def _graph_drive_item_path(self, item):
        if not isinstance(item, dict):
            return ""
        parent_reference = item.get("parentReference") or {}
        parent_path = parent_reference.get("path") or ""
        if ":" in parent_path:
            parent_path = parent_path.split(":", 1)[1]
        path = item.get("sourcePath") or item.get("path")
        if not path:
            path = "/".join(part for part in (parent_path.strip("/"), item.get("name")) if part)
        return unquote(path)

    def _graph_drive_item_path_parts(self, item):
        return [part for part in self._graph_drive_item_path(item).split("/") if part]

    def _graph_page_slug(self, page):
        url = page.get("webUrl") if isinstance(page, dict) else False
        if url:
            return Path(urlsplit(url).path).stem
        return Path(page.get("name") or "").stem if isinstance(page, dict) else ""

    def _graph_url_key(self, url, page=False):
        path = self._graph_path_key(url, page)
        if not path:
            return False
        parsed = urlsplit(self._graph_absolute_url(url, page) or "")
        host = (parsed.netloc or "").lower()
        return f"{host}{path}" if host else path

    def _graph_path_key(self, url, page=False):
        absolute_url = self._graph_absolute_url(url, page)
        if not absolute_url or not isinstance(absolute_url, str):
            return False
        clean_url = absolute_url.split("?", 1)[0]
        parsed = urlsplit(clean_url)
        path = parsed.path or clean_url
        return unquote(path).rstrip("/").lower()

    def _normalize_sharepoint_name(self, value):
        if not value:
            return ""
        value = unicodedata.normalize("NFKD", str(value))
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
        return value

    def _graph_datetime(self, value):
        if not value:
            return False
        try:
            parsed = isoparse(value)
        except (TypeError, ValueError):
            return False
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return fields.Datetime.to_string(parsed)

    def _get_or_create_imported_document(
        self,
        url,
        media_path=False,
        page=False,
        source_item=False,
        stats=None,
    ):
        if isinstance(source_item, dict) and source_item.get("downloadSkipped"):
            return self._get_or_create_imported_document_placeholder(
                url,
                page=page,
                source_item=source_item,
                stats=stats,
            )
        if not media_path:
            return False
        media_path = Path(media_path).expanduser()
        filename = self._graph_drive_item_filename(source_item) or self._local_media_filename(url)
        if not filename:
            return False
        local_file = self._find_local_media_file(media_path, url, source_item=source_item)
        if not local_file.exists():
            return False
        Document = self.env["documents.document"].sudo()
        destination_folder = self._get_graph_import_folder(filename, page=page)
        drive_id, drive_item_id = self._graph_drive_item_identity(source_item)
        existing = self.env["documents.document"]
        if drive_id and drive_item_id:
            existing = Document.search([
                ("sharepoint_drive_id", "=", drive_id),
                ("sharepoint_drive_item_id", "=", drive_item_id),
                ("type", "!=", "folder"),
            ], limit=1)
            if existing and not Document.search([
                ("id", "=", existing.id),
                ("id", "child_of", self.document_folder_id.id),
            ], limit=1):
                raise UserError(_(
                    "Položka SharePoint %(drive)s/%(item)s už patrí do iného tímu.",
                    drive=drive_id,
                    item=drive_item_id,
                ))
        if not existing:
            search_folder_ids = [destination_folder.id]
            if destination_folder != self.document_folder_id:
                search_folder_ids.append(self.document_folder_id.id)
            existing = Document.search([
                ("folder_id", "in", search_folder_ids),
                ("name", "=", filename),
                ("type", "!=", "folder"),
                ("sharepoint_drive_item_id", "=", False),
            ], limit=1)
        metadata_vals = self._graph_document_metadata_vals(
            filename,
            url,
            page=page,
            source_item=source_item,
        )
        if existing:
            vals = {
                **metadata_vals,
                "name": filename,
                "type": "binary",
                "url": False,
                "folder_id": destination_folder.id,
                "company_id": self.company_id.id,
            }
            if self._graph_document_content_changed(existing, source_item):
                vals.update({
                    "datas": base64.b64encode(local_file.read_bytes()),
                    "mimetype": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                })
                if stats is not None:
                    stats["documents_updated"] += 1
            elif stats is not None:
                stats["documents_unchanged"] += 1
            existing.write(vals)
            return existing
        mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if stats is not None:
            stats["documents_created"] += 1
        return Document.create({
            **metadata_vals,
            "name": filename,
            "folder_id": destination_folder.id,
            "company_id": self.company_id.id,
            "datas": base64.b64encode(local_file.read_bytes()),
            "mimetype": mimetype,
            "access_internal": "none",
            "access_via_link": "none",
            "is_access_via_link_hidden": True,
        })

    def _get_or_create_imported_document_placeholder(self, url, page=False, source_item=False, stats=None):
        filename = self._graph_drive_item_filename(source_item) or self._local_media_filename(url)
        source_url = self._graph_drive_item_url(source_item) or url
        if not filename or not source_url:
            return False
        Document = self.env["documents.document"].sudo()
        destination_folder = self._get_graph_import_folder(filename, page=page)
        drive_id, drive_item_id = self._graph_drive_item_identity(source_item)
        existing = Document.search([
            ("sharepoint_drive_id", "=", drive_id),
            ("sharepoint_drive_item_id", "=", drive_item_id),
            ("type", "!=", "folder"),
        ], limit=1) if drive_id and drive_item_id else self.env["documents.document"]
        if existing and not Document.search([
            ("id", "=", existing.id),
            ("id", "child_of", self.document_folder_id.id),
        ], limit=1):
            raise UserError(_(
                "Položka SharePoint %(drive)s/%(item)s už patrí do iného tímu.",
                drive=drive_id,
                item=drive_item_id,
            ))
        metadata_vals = self._graph_document_metadata_vals(
            filename,
            source_url,
            page=page,
            source_item=source_item,
        )
        if stats is not None:
            stats["documents_placeholders"] += 1
        if existing:
            vals = {
                **metadata_vals,
                "name": filename,
                "folder_id": destination_folder.id,
                "company_id": self.company_id.id,
            }
            if existing.type == "url":
                vals["url"] = source_url
            existing.write(vals)
            return existing
        return Document.create({
            **metadata_vals,
            "name": filename,
            "type": "url",
            "url": source_url,
            "folder_id": destination_folder.id,
            "company_id": self.company_id.id,
            "access_internal": "none",
            "access_via_link": "none",
            "is_access_via_link_hidden": True,
        })

    def _get_graph_import_folder(self, filename, page=False):
        self.ensure_one()
        if self.hr_portal_managed and self._is_hr_portal_background_document_name(filename):
            return self._ensure_hr_portal_background_folder()
        if page:
            return self._get_or_create_graph_page_folder(page)
        return self.document_folder_id

    def _get_or_create_graph_page_folder(self, page):
        self.ensure_one()
        source_id = page.get("id") or page.get("webUrl") or page.get("name")
        title = page.get("title") or Path(page.get("name") or "").stem or _("Bez názvu")
        Document = self.env["documents.document"].sudo()
        if source_id:
            folder = Document.search([
                ("type", "=", "folder"),
                ("folder_id", "=", self.document_folder_id.id),
                ("sharepoint_source_page_id", "=", source_id),
            ], limit=1)
            if folder:
                return folder

        folder_name = self._graph_page_folder_name(title, source_id)
        folder = Document.search([
            ("type", "=", "folder"),
            ("folder_id", "=", self.document_folder_id.id),
            ("name", "=", folder_name),
        ], limit=1)
        vals = {
            "name": folder_name,
            "type": "folder",
            "folder_id": self.document_folder_id.id,
            "company_id": self.company_id.id,
            "owner_id": False,
            "access_internal": "none",
            "access_via_link": "none",
            "is_access_via_link_hidden": True,
            "sharepoint_source_page_id": source_id,
            "sharepoint_source_page_title": title,
            "sharepoint_source_url": page.get("webUrl"),
        }
        if folder:
            folder.write(vals)
            return folder
        folder = Document.create(vals)
        self._sync_document_access()
        return folder

    def _graph_page_folder_name(self, title, source_id=False):
        base_name = str(title or _("Bez názvu")).strip() or _("Bez názvu")
        existing = self.env["documents.document"].sudo().search([
            ("type", "=", "folder"),
            ("folder_id", "=", self.document_folder_id.id),
            ("name", "=", base_name),
        ], limit=1)
        if not existing:
            return base_name
        if source_id and existing.sharepoint_source_page_id == source_id:
            return base_name
        suffix_source = source_id or base_name
        suffix = hashlib.sha1(str(suffix_source).encode()).hexdigest()[:8]
        return f"{base_name} ({suffix})"

    def _graph_document_metadata_vals(self, filename, url, page=False, source_item=False):
        drive_id, drive_item_id = self._graph_drive_item_identity(source_item)
        source_url = self._graph_drive_item_url(source_item) or url
        vals = {
            "sharepoint_drive_id": drive_id,
            "sharepoint_drive_item_id": drive_item_id,
            "sharepoint_source_url": source_url,
            "sharepoint_source_etag": (source_item or {}).get("eTag") or (source_item or {}).get("@odata.etag"),
            "sharepoint_source_modified": self._graph_drive_item_modified(source_item),
            "sharepoint_source_path": self._graph_drive_item_path(source_item) if source_item else False,
        }
        if page:
            vals.update({
                "sharepoint_source_page_id": page.get("id") or page.get("webUrl") or page.get("name"),
                "sharepoint_source_page_title": page.get("title") or page.get("name"),
            })
        return vals

    def _graph_document_content_changed(self, document, source_item=False):
        if document.type != "binary":
            return True
        if not source_item:
            return False
        source_etag = source_item.get("eTag") or source_item.get("@odata.etag")
        if source_etag and source_etag != document.sharepoint_source_etag:
            return True
        source_modified = self._graph_drive_item_modified(source_item)
        document_modified = (
            fields.Datetime.to_string(document.sharepoint_source_modified)
            if document.sharepoint_source_modified
            else False
        )
        return bool(source_modified and source_modified != document_modified)

    def _get_or_create_imported_image_url(self, url, media_path=False):
        if not media_path or not url or not self._looks_like_file_url(url, image=True):
            return False
        filename = self._local_media_filename(url)
        local_file = self._find_local_media_file(media_path, url)
        if not filename or not local_file.exists():
            return False
        mimetype = mimetypes.guess_type(filename)[0] or "image/jpeg"
        Attachment = self.env["ir.attachment"].sudo()
        existing = Attachment.search([
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
            ("name", "=", filename),
            ("mimetype", "=like", "image/%"),
        ], limit=1)
        if not existing:
            existing = Attachment.create({
                "name": filename,
                "res_model": self._name,
                "res_id": self.id,
                "datas": base64.b64encode(local_file.read_bytes()),
                "mimetype": mimetype,
            })
        return "/web/image/%s" % existing.id

    def _get_or_create_knowledge_cover(self, url, media_path=False):
        if not url or not self._looks_like_file_url(url, image=True):
            return False
        filename = self._local_media_filename(url)
        if not filename:
            return False

        Attachment = self.env["ir.attachment"].sudo()
        Cover = self.env["knowledge.cover"].sudo()
        local_file = media_path and self._find_local_media_file(media_path, url)
        if local_file and local_file.exists():
            mimetype = mimetypes.guess_type(filename)[0] or "image/jpeg"
            attachment = Attachment.search([
                ("res_model", "=", "knowledge.cover"),
                ("name", "=", filename),
                ("mimetype", "=like", "image/%"),
            ], limit=1)
            if not attachment:
                attachment = Attachment.create({
                    "name": filename,
                    "datas": base64.b64encode(local_file.read_bytes()),
                    "mimetype": mimetype,
                })
            cover = Cover.search([("attachment_id", "=", attachment.id)], limit=1)
            return cover or Cover.create({"attachment_id": attachment.id})

        attachment = Attachment.search([
            ("type", "=", "url"),
            ("url", "=", url),
            ("mimetype", "=like", "image/%"),
        ], limit=1)
        if not attachment:
            attachment = Attachment.create({
                "name": filename,
                "type": "url",
                "url": url,
                "mimetype": mimetypes.guess_type(filename)[0] or "image/jpeg",
            })
        cover = Cover.search([("attachment_id", "=", attachment.id)], limit=1)
        return cover or Cover.create({"attachment_id": attachment.id})

    def _local_media_filename(self, url):
        if not url:
            return False
        path = urlsplit(url.split("?", 1)[0]).path or url.split("?", 1)[0]
        return unquote(Path(path).name)

    def _find_local_media_file(self, media_path, url, source_item=False):
        for key in ("localFileName", "local_filename"):
            local_filename = isinstance(source_item, dict) and source_item.get(key)
            if local_filename:
                local_file = media_path / local_filename
                if local_file.exists():
                    return local_file
        filename = self._graph_drive_item_filename(source_item) or self._local_media_filename(url)
        if not filename:
            return Path("")
        for source_url in [url, *self._graph_drive_item_urls(source_item)]:
            if source_url:
                hashed = media_path / self._local_media_hashed_filename(source_url)
                if hashed.exists():
                    return hashed
        direct = media_path / filename
        if direct.exists():
            return direct
        matches = glob.glob(str(media_path / ("*_" + filename)))
        return Path(matches[0]) if matches else direct

    def _local_media_hashed_filename(self, url):
        filename = self._local_media_filename(url)
        digest = hashlib.sha1(url.encode()).hexdigest()[:12]
        return f"{digest}_{filename}"


class SharePointTeamMember(models.Model):
    _name = "sharepoint.team.member"
    _description = "Člen tímu SharePoint"
    _order = "role, user_id"

    team_id = fields.Many2one(
        "sharepoint.team",
        string="Tím",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Používateľ",
        required=True,
        ondelete="cascade",
        index=True,
        domain=[("share", "=", False)],
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        related="user_id.partner_id",
        store=True,
        readonly=True,
    )
    role = fields.Selection(
        [("owner", "Vlastník"), ("admin", "Administrátor"), ("member", "Používateľ"), ("visitor", "Návštevník")],
        required=True,
        default="member",
        string="Rola",
    )
    source = fields.Selection(
        [("manual", "Manuálne"), ("hr_employee", "HR zamestnanec")],
        required=True,
        default="manual",
        index=True,
        string="Zdroj",
    )

    _unique_team_user = models.Constraint(
        "UNIQUE(team_id, user_id)",
        "Tento používateľ už je členom tímu.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        members.team_id._sync_native_access()
        return members

    def write(self, vals):
        if (
            not self.env.context.get("sharepoint_hr_portal_sync")
            and {"user_id", "role"} & set(vals)
            and "source" not in vals
        ):
            vals = {**vals, "source": "manual"}
        res = super().write(vals)
        if {"user_id", "role"} & set(vals):
            self.team_id._sync_native_access()
        return res

    def unlink(self):
        teams = self.team_id
        res = super().unlink()
        teams._sync_native_access()
        return res
