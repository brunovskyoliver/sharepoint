from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SharePointTeam(models.Model):
    _name = "sharepoint.team"
    _description = "SharePoint Team"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    description = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    color = fields.Integer()
    member_ids = fields.One2many(
        "sharepoint.team.member",
        "team_id",
        string="Members",
        copy=True,
    )
    owner_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_owner_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Owners",
    )
    admin_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_admin_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Admins",
    )
    accessible_user_ids = fields.Many2many(
        "res.users",
        "sharepoint_team_accessible_user_rel",
        "team_id",
        "user_id",
        compute="_compute_access_users",
        store=True,
        string="Users With Access",
    )
    synced_partner_ids = fields.Many2many(
        "res.partner",
        "sharepoint_team_synced_partner_rel",
        "team_id",
        "partner_id",
        copy=False,
        string="Synchronized Partners",
    )
    document_folder_id = fields.Many2one(
        "documents.document",
        readonly=True,
        copy=False,
        string="Document Library",
    )
    knowledge_article_id = fields.Many2one(
        "knowledge.article",
        readonly=True,
        copy=False,
        string="Team Page",
    )
    document_count = fields.Integer(compute="_compute_content_counts")
    page_count = fields.Integer(compute="_compute_content_counts")
    user_role = fields.Selection(
        [("owner", "Owner"), ("admin", "Admin"), ("member", "User"), ("visitor", "Visitor")],
        compute="_compute_user_role",
    )

    _unique_name_company = models.Constraint(
        "UNIQUE(name, company_id)",
        "A SharePoint team with this name already exists in this company.",
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
                raise ValidationError(_("A team must have at least one owner."))

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
        self._sync_native_access()
        return True

    def action_open_documents(self):
        self.ensure_one()
        if not self.document_folder_id:
            raise UserError(_("This team does not have a document library yet."))
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
            raise UserError(_("This team does not have a Knowledge page yet."))
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
        return _(
            "<h2>%(team)s</h2><p>Team news, notes, decisions, and useful links.</p>",
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


class SharePointTeamMember(models.Model):
    _name = "sharepoint.team.member"
    _description = "SharePoint Team Member"
    _order = "role, user_id"

    team_id = fields.Many2one(
        "sharepoint.team",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="cascade",
        index=True,
        domain=[("share", "=", False)],
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="user_id.partner_id",
        store=True,
        readonly=True,
    )
    role = fields.Selection(
        [("owner", "Owner"), ("admin", "Admin"), ("member", "User"), ("visitor", "Visitor")],
        required=True,
        default="member",
    )

    _unique_team_user = models.Constraint(
        "UNIQUE(team_id, user_id)",
        "This user is already a member of the team.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        members = super().create(vals_list)
        members.team_id._sync_native_access()
        return members

    def write(self, vals):
        res = super().write(vals)
        if {"user_id", "role"} & set(vals):
            self.team_id._sync_native_access()
        return res

    def unlink(self):
        teams = self.team_id
        res = super().unlink()
        teams._sync_native_access()
        return res
