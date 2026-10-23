from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.service.model import call_kw
from odoo.tests import HttpCase, JsonRpcException, TransactionCase, new_test_user, tagged


# Server-file importers: if dispatched they would read the (nonexistent) path
# and fail with UserError, not AccessError.
SERVER_ONLY_CALLS = (
    ("import_hr_portal_graph_pages", ["/nonexistent/export.json"]),
    ("import_graph_site_pages", [[], "/nonexistent/export.json"]),
    ("action_import_hr_portal_graph_pages", [[], "/nonexistent/export.json"]),
)


@tagged("post_install", "-at_install")
class TestSharePointImportAccess(TransactionCase):
    def test_server_file_imports_refuse_rpc_for_every_actor(self):
        internal = new_test_user(self.env, login="sp.boundary.internal", groups="base.group_user")
        portal = new_test_user(self.env, login="sp.boundary.portal", groups="base.group_portal")
        manager = new_test_user(
            self.env, login="sp.boundary.manager", groups="base.group_user,sharepoint.group_sharepoint_manager",
        )
        for actor in (internal, portal, manager, self.env.ref("base.user_admin")):
            team = self.env["sharepoint.team"].with_user(actor)
            for method, args in SERVER_ONLY_CALLS:
                with self.subTest(actor=actor.login, method=method):
                    with self.assertRaisesRegex(AccessError, "cannot be called remotely"):
                        call_kw(team, method, args, {})

    def test_access_sync_requires_team_write_authority(self):
        owner = new_test_user(self.env, login="sp.boundary.owner", groups="base.group_user")
        visitor = new_test_user(self.env, login="sp.boundary.visitor", groups="base.group_user")
        outsider = new_test_user(self.env, login="sp.boundary.outsider", groups="base.group_user")
        team = self.env["sharepoint.team"].create({
            "name": "Invented boundary team",
            "member_ids": [
                Command.create({"user_id": owner.id, "role": "owner"}),
                Command.create({"user_id": visitor.id, "role": "visitor"}),
            ],
        })
        with patch.object(type(team), "_ensure_team_resources") as ensure:
            for actor in (visitor, outsider):
                with self.subTest(actor=actor.login), self.assertRaises(AccessError):
                    call_kw(team.with_user(actor), "action_sync_access", [team.ids], {})
            ensure.assert_not_called()
        self.assertTrue(call_kw(team.with_user(owner), "action_sync_access", [team.ids], {}))


@tagged("post_install", "-at_install")
class TestSharePointImportHttp(HttpCase):
    def test_json_rpc_refuses_internal_and_portal_users(self):
        for login, groups in (("sp.http.internal", "base.group_user"), ("sp.http.portal", "base.group_portal")):
            new_test_user(self.env, login=login, password=f"{login}-pw", groups=groups)
            self.authenticate(login, f"{login}-pw")
            for method, args in SERVER_ONLY_CALLS:
                with self.subTest(actor=login, method=method):
                    with self.assertRaises(JsonRpcException) as error:
                        self.make_jsonrpc_request(f"/web/dataset/call_kw/sharepoint.team/{method}", {
                            "model": "sharepoint.team", "method": method, "args": args, "kwargs": {},
                        })
                    self.assertIn("AccessError", str(error.exception))
