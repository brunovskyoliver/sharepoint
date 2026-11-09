from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Create a Documents folder under "Projekty" for every existing project."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["tenenet.project"]._cron_sync_project_document_access()
