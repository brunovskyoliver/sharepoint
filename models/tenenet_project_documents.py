"""One Documents folder per project, under the shared "Projekty" folder.

Access is closed to everyone except TENENET administrators and the project's
responsible people (Programový riaditeľ, Odborný garant, Ďalšie zodpovedné
osoby). Documents managers see every folder through the Documents app itself.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError

_ROOT_XMLID = "sharepoint.documents_folder_projects"
_ADMIN_GROUP_XMLID = "tenenet_projects.group_tenenet_admin"
_SYNC_FIELDS = {"name", "odborny_garant_id", "project_manager_id", "additional_responsible_ids", "active"}


class TenenetProjectDocuments(models.Model):
    _inherit = "tenenet.project"

    document_folder_id = fields.Many2one(
        "documents.document",
        string="Priečinok dokumentov",
        copy=False,
        readonly=True,
        index="btree_not_null",
        ondelete="set null",
    )
    can_open_project_documents = fields.Boolean(
        compute="_compute_can_open_project_documents",
    )

    @api.depends_context("uid")
    @api.depends("odborny_garant_id.user_id", "project_manager_id.user_id", "additional_responsible_ids.user_id")
    def _compute_can_open_project_documents(self):
        is_admin = self._project_documents_user_is_admin()
        for project in self:
            project.can_open_project_documents = is_admin or (
                self.env.user.partner_id in project.sudo()._project_documents_responsible_partners()
            )

    # -- access -----------------------------------------------------------

    def _project_documents_user_is_admin(self):
        user = self.env.user
        return (
            self.env.su
            or user.has_group(_ADMIN_GROUP_XMLID)
            or user.has_group("documents.group_documents_manager")
        )

    @api.model
    def _project_documents_admin_partners(self):
        group = self.env.ref(_ADMIN_GROUP_XMLID, raise_if_not_found=False)
        if not group:
            return self.env["res.partner"]
        users = group.sudo().all_user_ids.filtered(lambda user: user.active and not user.share)
        return users.partner_id

    def _project_documents_responsible_partners(self):
        self.ensure_one()
        employees = self.odborny_garant_id | self.project_manager_id | self.additional_responsible_ids
        users = employees.user_id.filtered(lambda user: user.active and not user.share)
        return users.partner_id

    @api.model
    def _project_documents_root(self):
        return self.env.ref(_ROOT_XMLID).sudo()

    def _ensure_project_document_folders(self):
        Document = self.env["documents.document"].sudo()
        root = self._project_documents_root()
        for project in self.sudo().filtered(lambda project: not project.document_folder_id):
            project.document_folder_id = Document.create({
                "name": project.name,
                "type": "folder",
                "folder_id": root.id,
                "owner_id": False,
                "company_id": False,
                "access_internal": "none",
                "access_via_link": "none",
                "is_access_via_link_hidden": True,
            })
        return True

    @api.model
    def _sync_project_documents_root_access(self):
        """Admins only on the shared parent, written row by row.

        ``action_update_access_rights`` would cascade every change into all
        project folders below; each of those carries its own partners.
        """
        root = self._project_documents_root()
        if root.access_internal != "none" or root.access_via_link != "none":
            root.write({"access_internal": "none", "access_via_link": "none", "is_access_via_link_hidden": True})
        admins = self._project_documents_admin_partners()
        rows = root.access_ids
        rows.filtered(lambda row: row.partner_id not in admins).unlink()
        rows.filtered(lambda row: row.partner_id in admins and row.role != "edit").write({"role": "edit"})
        missing = admins - rows.partner_id
        if missing:
            root.write({"access_ids": [
                Command.create({"partner_id": partner.id, "role": "edit"}) for partner in missing
            ]})

    def _sync_project_document_access(self):
        """Give admins and responsible people edit access; remove everyone else.

        Owners of uploaded files keep seeing their own files (native Documents
        rule), which is why removal only touches explicit access rows.
        """
        admins = self._project_documents_admin_partners()
        Team = self.env["sharepoint.team"].sudo()
        Document = self.env["documents.document"].sudo()
        for project in self.sudo().filtered("document_folder_id"):
            folder = project.document_folder_id
            if folder.name != project.name:
                folder.name = project.name
            allowed = admins | project._project_documents_responsible_partners()
            partners = {partner: ("edit", False) for partner in allowed}
            documents = Document.search([("id", "child_of", folder.id)])
            for partner in documents.access_ids.partner_id - allowed:
                partners[partner] = (False, False)
            current = {access.partner_id: (access.role, access.expiration_date) for access in folder.access_ids}
            closed = folder.access_internal == "none" and folder.access_via_link == "none"
            if closed and all(current.get(partner) == role for partner, role in partners.items()):
                continue
            folder.action_update_access_rights(
                access_internal="none",
                access_via_link="none",
                is_access_via_link_hidden=True,
                partners=partners,
            )
            # ONLYOFFICE keeps its own per-document role table; keep it in step
            # like the team libraries do.
            Team._save_onlyoffice_roles_batched(documents, partners)
        return True

    @api.model
    def _cron_sync_project_document_access(self):
        projects = self.sudo().with_context(active_test=False).search([])
        projects._ensure_project_document_folders()
        self._sync_project_documents_root_access()
        projects._sync_project_document_access()

    # -- lifecycle ----------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        projects = super().create(vals_list)
        self._sync_project_documents_root_access()
        projects._ensure_project_document_folders()
        projects._sync_project_document_access()
        return projects

    def write(self, vals):
        result = super().write(vals)
        if _SYNC_FIELDS & set(vals) and not self.env.context.get("sharepoint_skip_project_documents_sync"):
            self._ensure_project_document_folders()
            self._sync_project_document_access()
        return result

    # -- UI ---------------------------------------------------------------

    def action_open_project_documents(self):
        self.ensure_one()
        if not self.can_open_project_documents:
            raise AccessError(_("Dokumenty projektu môžu otvoriť iba zodpovedné osoby projektu a administrátori."))
        self._sync_project_documents_root_access()
        self._ensure_project_document_folders()
        self._sync_project_document_access()
        folder = self.sudo().document_folder_id
        # Same client action as the team libraries: native Documents preview,
        # ONLYOFFICE editing and browser back navigation.
        return {
            "type": "ir.actions.client",
            "tag": "document_action_preference",
            "name": folder.display_name,
            "context": {
                "documents_init_folder_id": folder.id,
                "documents_unique_folder_id": folder.id,
                "searchpanel_default_user_folder_id": folder.id,
                "default_folder_id": folder.id,
            },
            "target": "self",
        }
