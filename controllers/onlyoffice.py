import json
from urllib.parse import urlsplit

from odoo import _, http

from odoo.addons.onlyoffice_odoo_documents.controllers.controllers import OnlyofficeDocuments_Connector


def template_url_allowed(url):
    """Only the public OnlyOffice form gallery may be downloaded as a template."""
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and not parts.port and not parts.username
            and (host == "onlyoffice.com" or host.endswith(".onlyoffice.com")))


class SharepointOnlyofficeDocumentsCreate(OnlyofficeDocuments_Connector):
    @http.route("/onlyoffice/documents/file/create", auth="user", methods=["POST"], type="jsonrpc")
    def post_file_create(self, folder_id, supported_format, title, url=None):
        # The connector downloads any client-supplied URL on the Odoo server and
        # stores the response as the caller's document (server-side request
        # forgery into the LAN and localhost).
        if url and not template_url_allowed(url):
            return json.dumps({"error": _("Šablónu z tejto adresy nie je možné stiahnuť."),
                               "file_id": None, "document_id": None})
        return super().post_file_create(folder_id, supported_format, title, url=url)
