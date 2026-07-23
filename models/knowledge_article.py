from odoo import api, fields, models


class KnowledgeArticle(models.Model):
    _inherit = "knowledge.article"

    _HELPDESK_HELP_XMLID = "website_helpdesk_knowledge.helpdesk_knwoledge_article_help"

    sharepoint_source_id = fields.Char(string="ID zdroja SharePoint", copy=False, index=True)
    sharepoint_source_url = fields.Char(string="URL zdroja SharePoint", copy=False)
    sharepoint_source_modified = fields.Datetime(string="Zmenené v zdroji SharePoint", copy=False)
    sharepoint_source_author = fields.Char(string="Autor zdroja SharePoint", copy=False)

    _unique_sharepoint_source_id = models.Constraint(
        "UNIQUE(parent_id, sharepoint_source_id)",
        "Stránka SharePoint s týmto ID zdroja už bola importovaná do tohto tímu.",
    )

    @api.model
    def _sharepoint_remove_helpdesk_help_article(self):
        """Remove Odoo's stock Helpdesk Knowledge page from SharePoint databases.

        Recreate its XML ID after deleting the article: its source record uses
        ``forcecreate=False``, so future upgrades of
        ``website_helpdesk_knowledge`` cannot recreate it.
        """
        module, name = self._HELPDESK_HELP_XMLID.split(".")
        data = self.env["ir.model.data"].sudo().search([
            ("module", "=", module),
            ("name", "=", name),
            ("model", "=", self._name),
        ], limit=1)
        article = self.browse(data.res_id).exists() if data else self.browse()
        if article:
            article_id = data.res_id
            noupdate = data.noupdate
            article.sudo().unlink()
            self.env["ir.model.data"].sudo().create({
                "module": module,
                "name": name,
                "model": self._name,
                "res_id": article_id,
                "noupdate": noupdate,
            })
        elif not data:
            self.env["ir.model.data"].sudo().create({
                "module": module,
                "name": name,
                "model": self._name,
                "res_id": 0,
                "noupdate": True,
            })

    @api.model
    def _sharepoint_remove_knowledge_welcome_articles(self):
        """Remove generated personal Knowledge welcome pages from SharePoint."""
        welcome_articles = self.sudo().search([
            ("icon", "=", "👋"),
            "|",
            ("body", "ilike", "This private page is for you to play around with."),
            ("body", "ilike", "Táto súkromná stránka je priestor, kde si môžete Knowledge vyskúšať."),
        ])
        welcome_articles.unlink()
