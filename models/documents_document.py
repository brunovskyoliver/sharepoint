from odoo import Command, api, fields, models
from odoo.fields import Domain


class DocumentsDocument(models.Model):
    _inherit = "documents.document"

    sharepoint_drive_id = fields.Char(string="SharePoint Drive ID", copy=False, index=True)
    sharepoint_drive_item_id = fields.Char(string="SharePoint Drive Item ID", copy=False, index=True)
    sharepoint_source_url = fields.Char(string="SharePoint Source URL", copy=False)
    sharepoint_source_etag = fields.Char(string="SharePoint Source eTag", copy=False)
    sharepoint_source_modified = fields.Datetime(string="SharePoint Source Modified", copy=False)
    sharepoint_source_page_id = fields.Char(string="SharePoint Source Page ID", copy=False, index=True)
    sharepoint_source_page_title = fields.Char(string="SharePoint Source Page Title", copy=False)
    sharepoint_source_path = fields.Char(string="SharePoint Source Path", copy=False)

    _unique_sharepoint_drive_item = models.Constraint(
        "UNIQUE(sharepoint_drive_id, sharepoint_drive_item_id)",
        "A SharePoint drive item has already been imported.",
    )

    def _search_user_permission(self, operator, value, exclude_ownership=False):
        domain = super()._search_user_permission(operator, value, exclude_ownership=exclude_ownership)
        if (
            domain is NotImplemented
            or self.env.context.get("sharepoint_skip_documents_scope")
            or self.env.user.share
            or self.env.user.has_group("documents.group_documents_manager")
        ):
            return domain
        return domain & self._sharepoint_user_documents_scope_domain()

    @api.model
    def _sharepoint_user_documents_scope_domain(self):
        user = self.env.user
        partner = user.partner_id
        access_domain = Domain("access_ids", "any", Domain.AND([
            Domain("partner_id", "=", partner.id),
            Domain("role", "in", ("view", "edit")),
            Domain("expiration_date", "=", False) | Domain("expiration_date", ">", fields.Datetime.now()),
        ]))
        owner_domain = Domain("owner_id", "=", user.id)
        hr_portal_domain = Domain.FALSE
        team = self.env["sharepoint.team"].sudo()._get_existing_hr_portal_team()
        if team and team.document_folder_id:
            hr_portal_domain = Domain("id", "child_of", team.document_folder_id.id)
        return owner_domain | access_domain | hr_portal_domain

    @api.model
    def search_panel_select_range(self, field_name, **kwargs):
        result = super().search_panel_select_range(field_name, **kwargs)
        if field_name != "user_folder_id":
            return result

        values = result.get("values") or []
        for value in values:
            if value.get("id") == "COMPANY":
                value["display_name"] = "TENENET o.z."

        if (
            self.env.context.get("documents_unique_folder_id")
            or self.env.user.has_group("documents.group_documents_manager")
        ):
            return result

        allowed_folder_ids = self._sharepoint_hr_portal_search_panel_folder_ids()
        if not allowed_folder_ids:
            result["values"] = [
                value for value in values if not isinstance(value.get("id"), int)
            ]
            return result

        result["values"] = [
            value
            for value in values
            if not isinstance(value.get("id"), int) or value["id"] in allowed_folder_ids
        ]
        return result

    @api.model
    def _sharepoint_hr_portal_search_panel_folder_ids(self):
        team = self.env["sharepoint.team"].sudo()._get_existing_hr_portal_team()
        if not team or not team.document_folder_id:
            return set()
        folders = self.sudo().search([
            ("type", "=", "folder"),
            ("id", "child_of", team.document_folder_id.id),
        ])
        return set(folders.ids)

    @api.model
    def _sharepoint_ensure_documents_user_defaults(self):
        group = self.env.ref("documents.group_documents_user", raise_if_not_found=False)
        default_group = self.env.ref("base.default_user_group", raise_if_not_found=False)
        if not group:
            return
        if default_group and group not in default_group.implied_ids:
            default_group.sudo().write({"implied_ids": [Command.link(group.id)]})
        self.env["res.users"].sudo()._sharepoint_ensure_default_documents_user_group()
