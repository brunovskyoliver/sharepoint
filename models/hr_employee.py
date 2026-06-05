from odoo import api, models


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    @api.model_create_multi
    def create(self, vals_list):
        employees = super().create(vals_list)
        if not self.env.context.get("sharepoint_skip_hr_portal_sync"):
            self.env["sharepoint.team"].sudo()._sync_hr_portal_employee_visitors_all()
        return employees

    def write(self, vals):
        res = super().write(vals)
        if (
            not self.env.context.get("sharepoint_skip_hr_portal_sync")
            and {"active", "user_id"} & set(vals)
        ):
            self.env["sharepoint.team"].sudo()._sync_hr_portal_employee_visitors_all()
        return res
