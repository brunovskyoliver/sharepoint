from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["sharepoint.team"]._sync_hr_portal_employee_visitors_all()
