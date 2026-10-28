from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.service.model import call_kw
from odoo.tests import HttpCase, JsonRpcException, TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSharePointImportAccess(TransactionCase):
    def test_filesystem_imports_are_private_for_all_rpc_actors(self):
        internal = new_test_user(self.env, login="sharepoint.boundary.internal", groups="base.group_user")
        portal = new_test_user(self.env, login="sharepoint.boundary.portal", groups="base.group_portal")
        for actor in (internal, portal, self.env.ref("base.user_admin")):
            team = self.env["sharepoint.team"].with_user(actor)
            for method, args in (
                ("import_hr_portal_graph_pages", ["/not-opened.json"]),
                ("import_graph_site_pages", [[], "/not-opened.json"]),
                ("action_import_hr_portal_graph_pages", [[], "/not-opened.json"]),
            ):
                with self.subTest(actor=actor.id, method=method):
                    with self.assertRaises(AccessError):
                        call_kw(team, method, args, {})

    def test_native_access_sync_requires_team_write_authority(self):
        owner = new_test_user(self.env, login="sharepoint.boundary.owner", groups="base.group_user")
        visitor = new_test_user(self.env, login="sharepoint.boundary.visitor", groups="base.group_user")
        team = self.env["sharepoint.team"].create({
            "name": "Invented import boundary team",
            "member_ids": [Command.create({"user_id": owner.id, "role": "owner"}),
                           Command.create({"user_id": visitor.id, "role": "visitor"})],
        })
        with patch.object(type(team), "_ensure_team_resources") as ensure:
            with self.assertRaises(AccessError):
                call_kw(team.with_user(visitor), "action_sync_access", [team.ids], {})
            ensure.assert_not_called()
        self.assertTrue(call_kw(team.with_user(owner), "action_sync_access", [team.ids], {}))


@tagged("post_install", "-at_install")
class TestLegacyImportHTTPAccess(HttpCase):
    def test_authenticated_http_cannot_invoke_maintenance_imports(self):
        for login, groups in (("import.http.internal", "base.group_user"),
                              ("import.http.portal", "base.group_portal")):
            new_test_user(self.env, login=login, password="invented-http-password", groups=groups)
            self.authenticate(login, "invented-http-password")
            for model, method, args in (
                ("sharepoint.team", "import_hr_portal_graph_pages", ["/not-opened.json"]),
                ("sharepoint.team", "import_graph_site_pages", [[], "/not-opened.json"]),
                ("sharepoint.team", "action_import_hr_portal_graph_pages", [[], "/not-opened.json"]),
                ("tenenet.bookio.importer", "import_rows", [[], []]),
                ("tenenet.bookio.importer", "import_csv_path", [[], "/not-opened.csv"]),
            ):
                with self.subTest(actor=login, method=method):
                    with self.assertRaises(JsonRpcException) as error:
                        self.make_jsonrpc_request(f"/web/dataset/call_kw/{model}/{method}", {
                            "model": model, "method": method, "args": args, "kwargs": {},
                        })
                    self.assertIn("Access", str(error.exception))
