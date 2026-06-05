import base64
from unittest.mock import patch

from odoo import Command
from odoo.addons.tenenet_projects.models.tenenet_employee_evaluation import TenenetEmployeeEvaluation
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open


@tagged("post_install", "-at_install")
class TestSharePointTeam(TransactionCase):
    def setUp(self):
        super().setUp()
        self.owner = self._create_user("sp.owner")
        self.member = self._create_user("sp.member")
        self.visitor = self._create_user("sp.visitor")

    def _create_user(self, login):
        return self.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": f"{login}@example.com",
            "email": f"{login}@example.com",
            "group_ids": [Command.set([self.env.ref("base.group_user").id])],
        })

    def test_team_creation_creates_native_resources_and_access(self):
        team = self.env["sharepoint.team"].create({
            "name": "Operations",
            "member_ids": [
                Command.create({"user_id": self.owner.id, "role": "owner"}),
                Command.create({"user_id": self.member.id, "role": "member"}),
                Command.create({"user_id": self.visitor.id, "role": "visitor"}),
            ],
        })

        self.assertTrue(team.document_folder_id)
        self.assertTrue(team.knowledge_article_id)
        self.assertEqual(team.document_folder_id.access_internal, "none")
        self.assertEqual(team.document_folder_id.access_via_link, "none")

        access_by_partner = {
            access.partner_id: access.role
            for access in team.document_folder_id.access_ids
        }
        self.assertEqual(access_by_partner[self.owner.partner_id], "edit")
        self.assertEqual(access_by_partner[self.member.partner_id], "edit")
        self.assertEqual(access_by_partner[self.visitor.partner_id], "view")

        page_members = {
            member.partner_id: member.permission
            for member in team.knowledge_article_id.article_member_ids
        }
        self.assertEqual(page_members[self.owner.partner_id], "write")
        self.assertEqual(page_members[self.member.partner_id], "write")
        self.assertEqual(page_members[self.visitor.partner_id], "read")

    def test_removed_team_user_loses_synchronized_access(self):
        team = self.env["sharepoint.team"].create({
            "name": "Finance",
            "member_ids": [
                Command.create({"user_id": self.owner.id, "role": "owner"}),
                Command.create({"user_id": self.member.id, "role": "member"}),
            ],
        })

        team.member_ids.filtered(lambda rec: rec.user_id == self.member).unlink()

        self.assertNotIn(self.member.partner_id, team.document_folder_id.access_ids.partner_id)
        self.assertNotIn(self.member.partner_id, team.knowledge_article_id.article_member_ids.partner_id)

    def test_signed_evaluation_pdf_is_saved_under_employee_documents(self):
        manager = self.env["hr.employee"].create({
            "name": "Manager",
            "user_id": self.owner.id,
            "work_email": self.owner.email,
        })
        employee = self.env["hr.employee"].create({
            "name": "Employee",
            "user_id": self.member.id,
            "parent_id": manager.id,
            "work_email": self.member.email,
        })
        evaluation = self.env["tenenet.employee.evaluation"].with_user(self.owner).create({
            "employee_id": employee.id,
            "manager_id": manager.id,
            "year": 2026,
        })
        evaluation.line_ids[:1].with_user(self.owner).write({
            "is_awarded": True,
            "impact_note": "Concrete team impact.",
        })
        with patch.object(TenenetEmployeeEvaluation, "_render_pdf_for_sign", return_value=self._sample_pdf_data()):
            evaluation.with_user(self.owner).action_publish()

        completed_document = self.env["sign.completed.document"].sudo().create({
            "sign_request_id": evaluation.sign_request_id.id,
            "document_id": evaluation.sign_template_id.document_ids[:1].id,
            "file": self._sample_pdf_data(),
        })
        evaluation.sign_request_id.sudo().write({"state": "signed"})
        evaluation.invalidate_recordset(["sign_state"])
        evaluation._store_signed_document_if_needed()

        self.assertTrue(completed_document.file)
        self.assertEqual(evaluation.signed_document_id.folder_id.name, "Ročné hodnotenia")
        self.assertEqual(evaluation.signed_document_id.folder_id.folder_id, employee.hr_employee_folder_id)

    def _sample_pdf_data(self):
        with file_open("sign/static/demo/sample_contract.pdf", "rb") as pdf_file:
            return base64.b64encode(pdf_file.read())
