import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.sharepoint.controllers.onlyoffice import template_url_allowed


@tagged("post_install", "-at_install")
class TestOnlyofficeCreateGuard(HttpCase):
    def test_allowlist(self):
        self.assertTrue(template_url_allowed("https://oforms.onlyoffice.com/dashboard/api/uploads/form.docx"))
        for url in ("http://oforms.onlyoffice.com/a.docx", "https://127.0.0.1:8069/web/content/1",
                    "https://localhost/a.docx", "https://evil.example/onlyoffice.com/a.docx",
                    "https://x.onlyoffice.com.evil.example/a", "https://user@oforms.onlyoffice.com/a",
                    "https://oforms.onlyoffice.com:8443/a", "file:///etc/passwd", None, "", 5):
            self.assertFalse(template_url_allowed(url), url)

    def test_route_never_fetches_a_disallowed_url(self):
        self.env["res.users"].create({
            "name": "Guard Test", "login": "oo_guard_test", "password": "oo_guard_test_pw_1",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        self.authenticate("oo_guard_test", "oo_guard_test_pw_1")
        with patch("odoo.addons.onlyoffice_odoo_documents.controllers.controllers.requests.get") as fetch:
            result = self.make_jsonrpc_request("/onlyoffice/documents/file/create", {
                "folder_id": "MY", "supported_format": "docx", "title": "x",
                "url": "http://127.0.0.1:8069/web/database/manager",
            })
        fetch.assert_not_called()
        self.assertTrue(json.loads(result)["error"])
        self.assertFalse(self.env["documents.document"].search([("name", "=", "x.docx")]))
