from odoo import fields, models


class KnowledgeArticle(models.Model):
    _inherit = "knowledge.article"

    sharepoint_source_id = fields.Char(string="ID zdroja SharePoint", copy=False, index=True)
    sharepoint_source_url = fields.Char(string="URL zdroja SharePoint", copy=False)
    sharepoint_source_modified = fields.Datetime(string="Zmenené v zdroji SharePoint", copy=False)
    sharepoint_source_author = fields.Char(string="Autor zdroja SharePoint", copy=False)

    _unique_sharepoint_source_id = models.Constraint(
        "UNIQUE(sharepoint_source_id)",
        "Stránka SharePoint s týmto ID zdroja už bola importovaná.",
    )
