from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["knowledge.article"]._sharepoint_remove_helpdesk_help_article()
