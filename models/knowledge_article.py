from odoo import fields, models


class KnowledgeArticle(models.Model):
    _inherit = "knowledge.article"

    sharepoint_source_id = fields.Char(string="SharePoint Source ID", copy=False, index=True)
    sharepoint_source_url = fields.Char(string="SharePoint Source URL", copy=False)
    sharepoint_source_modified = fields.Datetime(string="SharePoint Source Modified", copy=False)
    sharepoint_source_author = fields.Char(string="SharePoint Source Author", copy=False)

    _unique_sharepoint_source_id = models.Constraint(
        "UNIQUE(sharepoint_source_id)",
        "A SharePoint page with this source ID has already been imported.",
    )
