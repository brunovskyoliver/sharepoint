from odoo import Command, api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _sharepoint_ensure_default_documents_user_group(self):
        documents_user_group = self.env.ref(
            "documents.group_documents_user",
            raise_if_not_found=False,
        )
        internal_group = self.env.ref("base.group_user", raise_if_not_found=False)
        if not documents_user_group or not internal_group:
            return 0

        users = self.with_context(active_test=False).search([
            ("active", "=", True),
            ("share", "=", False),
            ("all_group_ids", "in", [internal_group.id]),
            ("all_group_ids", "not in", [documents_user_group.id]),
        ])
        if users:
            users.write({"group_ids": [Command.link(documents_user_group.id)]})
        return len(users)

    @api.model_create_multi
    def create(self, vals_list):
        return super(ResUsers, self.with_context(
            knowledge_skip_onboarding_article=True,
        )).create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if (
            not self.env.context.get("sharepoint_skip_hr_portal_sync")
            and {"active", "share"} & set(vals)
        ):
            self.env["sharepoint.team"].sudo()._sync_hr_portal_employee_visitors_all()
        return res

    @api.model
    def _tenenet_translate_knowledge_welcome_articles(self, create_missing=False):
        """SharePoint databases do not use personal Knowledge welcome pages."""
        return 0
