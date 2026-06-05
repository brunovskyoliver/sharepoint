from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["documents.document"]._sharepoint_ensure_documents_user_defaults()
    team = env["sharepoint.team"]._get_or_create_hr_portal_team()
    team._sync_hr_portal_background_documents()
    team._sync_hr_portal_employee_visitors()
