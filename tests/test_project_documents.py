from odoo import Command, http
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged


def _user(env, login, *groups):
    return new_test_user(env, login=login, password=f"{login}-pw",
                         groups=",".join(("base.group_user", "documents.group_documents_user") + groups))


class _ProjectDocumentsCase:
    @classmethod
    def _setup_project_documents(cls):
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       tenenet_skip_employee_created_alert=True,
                                       tenenet_employer_company_ids=cls.env.company.ids))
        cls.admin = _user(cls.env, "pd.admin", "tenenet_projects.group_tenenet_admin")
        cls.director = _user(cls.env, "pd.director")
        cls.guarantor = _user(cls.env, "pd.guarantor")
        cls.extra = _user(cls.env, "pd.extra")
        cls.outsider = _user(cls.env, "pd.outsider")
        Employee = cls.env["hr.employee"]
        cls.director_emp = Employee.create({"name": "PD riaditeľ", "user_id": cls.director.id})
        cls.guarantor_emp = Employee.create({"name": "PD garant", "user_id": cls.guarantor.id})
        cls.extra_emp = Employee.create({"name": "PD ďalší", "user_id": cls.extra.id})
        cls.outsider_emp = Employee.create({"name": "PD cudzí", "user_id": cls.outsider.id})
        cls.project = cls.env["tenenet.project"].create({
            "name": "Medzinárodný projekt PD",
            "odborny_garant_id": cls.director_emp.id,
            "project_manager_id": cls.guarantor_emp.id,
            "additional_responsible_ids": [Command.set(cls.extra_emp.ids)],
            "date_start": "2026-01-01", "date_end": "2026-12-31",
            "allocation_company_id": cls.env.company.id,
            "allocation_attribution_status": "verified",
            "allocation_attribution_evidence": "Synthetic project documents fixture",
        })
        cls.root = cls.env.ref("sharepoint.documents_folder_projects")

    def _visible_to(self, user, records):
        Document = self.env["documents.document"].with_user(user)
        return Document.search([("id", "in", records.ids)])


@tagged("post_install", "-at_install")
class TestProjectDocuments(_ProjectDocumentsCase, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_project_documents()

    def test_new_project_gets_closed_subfolder_under_projekty(self):
        folder = self.project.document_folder_id
        self.assertTrue(folder)
        self.assertEqual(folder.type, "folder")
        self.assertEqual(folder.folder_id, self.root)
        self.assertEqual(folder.name, self.project.name)
        self.assertEqual((folder.access_internal, folder.access_via_link), ("none", "none"))
        for user in (self.admin, self.director, self.guarantor, self.extra):
            with self.subTest(user=user.login):
                self.assertEqual(self._visible_to(user, folder), folder)
                self.assertEqual(folder.with_user(user).user_permission, "edit")
        self.assertFalse(self._visible_to(self.outsider, folder))
        # Only admins see the shared parent.
        self.assertEqual(self._visible_to(self.admin, self.root), self.root)
        self.assertFalse(self._visible_to(self.director, self.root))

    def test_changing_responsible_moves_access_including_files(self):
        folder = self.project.document_folder_id
        file = self.env["documents.document"].sudo().create({
            "name": "zmluva.pdf", "folder_id": folder.id, "owner_id": self.admin.id,
            "datas": "JVBERi0=",
        })
        self.assertEqual(self._visible_to(self.guarantor, file), file)

        self.project.write({"project_manager_id": self.outsider_emp.id})

        self.assertFalse(self._visible_to(self.guarantor, folder | file))
        self.assertEqual(self._visible_to(self.outsider, folder | file), folder | file)
        self.assertEqual(self._visible_to(self.director, folder | file), folder | file)

    def test_rename_follows_project(self):
        self.project.write({"name": "Premenovaný projekt PD"})
        self.assertEqual(self.project.document_folder_id.name, "Premenovaný projekt PD")

    def test_open_documents_button(self):
        for user in (self.admin, self.extra):
            with self.subTest(user=user.login):
                project = self.project.with_user(user)
                self.assertTrue(project.can_open_project_documents)
                action = project.action_open_project_documents()
                self.assertEqual(action["tag"], "document_action_preference")
                self.assertEqual(action["context"]["documents_unique_folder_id"], self.project.document_folder_id.id)
                self.assertNotIn("default_user_folder_id", action["context"])
        # Outsiders cannot open the project's details (tenenet read scope), so
        # the button is never shown to them; a direct call is refused.
        with self.assertRaises(AccessError):
            self.project.with_user(self.outsider).action_open_project_documents()

    def test_cron_backfills_projects_without_folder(self):
        legacy = self.env["tenenet.project"].with_context(sharepoint_skip_project_documents_sync=True).create({
            "name": "Starší projekt PD",
            "project_manager_id": self.guarantor_emp.id,
            "allocation_company_id": self.env.company.id,
            "allocation_attribution_status": "verified",
            "allocation_attribution_evidence": "Synthetic project documents fixture",
        })
        legacy.document_folder_id.unlink()
        self.assertFalse(legacy.document_folder_id)
        self.env["tenenet.project"]._cron_sync_project_document_access()
        self.assertEqual(legacy.document_folder_id.folder_id, self.root)
        self.assertEqual(self._visible_to(self.guarantor, legacy.document_folder_id), legacy.document_folder_id)


@tagged("post_install", "-at_install")
class TestProjectDocumentsUpload(_ProjectDocumentsCase, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_project_documents()

    def test_responsible_uploads_from_project_button(self):
        context = self.project.with_user(self.guarantor).action_open_project_documents()["context"]
        folder = self.project.document_folder_id
        self.authenticate(self.guarantor.login, "pd.guarantor-pw")
        data = {"csrf_token": http.Request.csrf_token(self)}
        data.update({
            key.removeprefix("default_"): context[key]
            for key in ("default_user_folder_id", "default_partner_id", "default_res_id", "default_res_model")
            if context.get(key)
        })
        response = self.url_open(
            f"/documents/upload/{folder.sudo().access_token}",
            data=data,
            files={"ufile": ("podany-projekt.txt", b"podany projekt", "text/plain")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        document = self.env["documents.document"].browse(response.json())
        self.assertEqual(document.folder_id, folder)
        self.assertFalse(self._visible_to(self.outsider, document))
        self.assertEqual(self._visible_to(self.director, document), document)

    def test_documents_button_navigation_tour(self):
        self.project.write({"name": "Tour projekt PD"})
        self.env["documents.document"].sudo().create({
            "name": "zmluva-tour.txt", "folder_id": self.project.document_folder_id.id,
            "owner_id": self.guarantor.id, "datas": "dGVzdA==",
        })
        self.guarantor.password = self.guarantor.login  # start_tour logs in with password = login
        self.start_tour(f"/odoo/tenenet.project/{self.project.id}", "sharepoint_project_documents_tour",
                        login=self.guarantor.login)
