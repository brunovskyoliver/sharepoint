from odoo import Command, http
from odoo.tests import HttpCase, new_test_user, tagged


# The Documents client copies these action context keys into the upload form
# (documents/static/src/core/document_service.js, uploadDocument).
_UPLOAD_CONTEXT_KEYS = ("default_user_folder_id", "default_partner_id", "default_res_id", "default_res_model")


@tagged("post_install", "-at_install")
class TestTeamDocumentsUpload(HttpCase):
    def test_member_uploads_into_team_library_from_documents_button(self):
        owner = new_test_user(self.env, login="sp.upload.owner", groups="base.group_user")
        member = new_test_user(self.env, login="sp.upload.member", password="sp.upload.member-pw",
                               groups="base.group_user")
        team = self.env["sharepoint.team"].create({
            "name": "Upload team",
            "member_ids": [
                Command.create({"user_id": owner.id, "role": "owner"}),
                Command.create({"user_id": member.id, "role": "member"}),
            ],
        })
        folder = team.document_folder_id
        context = team.with_user(member).action_open_documents()["context"]

        self.authenticate(member.login, "sp.upload.member-pw")
        data = {"csrf_token": http.Request.csrf_token(self)}
        data.update({
            key.removeprefix("default_"): context[key]
            for key in _UPLOAD_CONTEXT_KEYS if context.get(key)
        })
        response = self.url_open(
            f"/documents/upload/{folder.sudo().access_token}",
            data=data,
            files={"ufile": ("navrh.txt", b"podany projekt", "text/plain")},
        )

        self.assertEqual(response.status_code, 200, response.text)
        document = self.env["documents.document"].browse(response.json())
        self.assertEqual(document.name, "navrh.txt")
        self.assertEqual(document.folder_id, folder)
