from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    team = env["sharepoint.team"]._get_existing_hr_portal_team()
    if team:
        team._ensure_hr_portal_background_folder()
        team._sync_hr_portal_background_documents()
