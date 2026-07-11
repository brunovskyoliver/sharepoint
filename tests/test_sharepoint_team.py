import base64
import json
import tempfile
from unittest.mock import patch

from odoo import Command, fields
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

    def test_hr_portal_auto_adds_active_employee_user_as_visitor(self):
        employee_user = self._create_user("sp.employee")
        self.env["hr.employee"].create({
            "name": "Portal Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })

        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        member = team.member_ids.filtered(lambda rec: rec.user_id == employee_user)

        self.assertEqual(member.role, "visitor")
        self.assertEqual(member.source, "hr_employee")
        self.assertEqual(
            team.document_folder_id.access_ids.filtered(
                lambda access: access.partner_id == employee_user.partner_id
            ).role,
            "view",
        )
        self.assertEqual(
            team.knowledge_article_id.article_member_ids.filtered(
                lambda article_member: article_member.partner_id == employee_user.partner_id
            ).permission,
            "read",
        )

    def test_hr_portal_sync_preserves_manual_promotion(self):
        employee_user = self._create_user("sp.promoted")
        self.env["hr.employee"].create({
            "name": "Promoted Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        member = team.member_ids.filtered(lambda rec: rec.user_id == employee_user)

        member.write({"role": "member"})
        self.env["sharepoint.team"].sudo()._sync_hr_portal_employee_visitors_all()
        member.invalidate_recordset(["role", "source"])

        self.assertEqual(member.role, "member")
        self.assertEqual(member.source, "manual")

    def test_hr_portal_sync_removes_inactive_auto_user(self):
        employee_user = self._create_user("sp.inactive")
        self.env["hr.employee"].create({
            "name": "Inactive Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        self.assertIn(employee_user, team.member_ids.user_id)

        employee_user.sudo().write({"active": False})
        team.invalidate_recordset(["member_ids"])

        self.assertNotIn(employee_user, team.member_ids.user_id)

    def test_hr_portal_import_wrapper_refreshes_employee_visitors(self):
        employee_user = self._create_user("sp.import.employee")
        self.env["hr.employee"].create({
            "name": "Import Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        team.member_ids.filtered(lambda rec: rec.user_id == employee_user).unlink()
        team.invalidate_recordset(["member_ids"])
        self.assertNotIn(employee_user, team.member_ids.user_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            export_file = f"{tmpdir}/pages.json"
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump({"value": []}, handle)
            stats = self.env["sharepoint.team"].sudo().import_hr_portal_graph_pages(export_file)

        team.invalidate_recordset(["member_ids"])
        member = team.member_ids.filtered(lambda rec: rec.user_id == employee_user)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(member.role, "visitor")
        self.assertEqual(member.source, "hr_employee")

    def test_documents_user_defaults_are_backfilled_for_internal_users(self):
        user = self._create_user("sp.default.documents")
        documents_user_group = self.env.ref("documents.group_documents_user")
        self.assertNotIn(documents_user_group, user.all_group_ids)

        self.env["documents.document"]._sharepoint_ensure_documents_user_defaults()
        user.invalidate_recordset(["group_ids", "all_group_ids"])

        self.assertIn(documents_user_group, user.all_group_ids)

    def test_employee_documents_search_panel_is_limited_to_hr_portal(self):
        employee_user = self._create_user("sp.panel.employee")
        employee_user.write({
            "group_ids": [Command.link(self.env.ref("documents.group_documents_user").id)]
        })
        self.env["hr.employee"].create({
            "name": "Panel Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        team._sync_hr_portal_employee_visitors()

        values = self.env["documents.document"].with_user(employee_user).search_panel_select_range(
            "user_folder_id"
        )["values"]
        values_by_name = {value["display_name"]: value for value in values}

        self.assertEqual(values_by_name["TENENET o.z."]["id"], "COMPANY")
        self.assertIn("My Drive", values_by_name)
        self.assertIn("Shared with me", values_by_name)
        self.assertIn("Recent", values_by_name)
        self.assertIn("Trash", values_by_name)
        self.assertIn("HR portál", values_by_name)
        self.assertNotIn("Company", values_by_name)
        self.assertNotIn("Inbox", values_by_name)
        self.assertNotIn("Finance", values_by_name)
        self.assertEqual(values_by_name["HR portál"]["user_folder_id"], "COMPANY")

    def test_documents_user_recent_excludes_unscoped_company_documents(self):
        employee_user = self._create_user("sp.recent.employee")
        employee_user.write({
            "group_ids": [Command.link(self.env.ref("documents.group_documents_user").id)]
        })
        self.env["hr.employee"].create({
            "name": "Recent Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        team._sync_hr_portal_employee_visitors()
        Document = self.env["documents.document"].sudo()
        hidden_folder = Document.create({
            "name": "Sign",
            "type": "folder",
            "access_internal": "view",
            "company_id": self.env.company.id,
        })
        hidden_document = Document.create({
            "name": "sign-template.pdf",
            "folder_id": hidden_folder.id,
            "access_internal": "view",
            "company_id": self.env.company.id,
            "datas": self._sample_pdf_data(),
            "mimetype": "application/pdf",
        })
        self.env["documents.access"].sudo().create({
            "document_id": hidden_document.id,
            "partner_id": employee_user.partner_id.id,
            "last_access_date": fields.Datetime.now(),
        })

        visible_recent = self.env["documents.document"].with_user(employee_user).search([
            ("user_folder_id", "=", "RECENT"),
        ])

        self.assertNotIn(hidden_document, visible_recent)
        self.assertFalse(
            self.env["documents.document"].with_user(employee_user).search([
                ("id", "=", hidden_document.id),
            ])
        )

    def test_documents_manager_search_panel_keeps_company_folders_with_renamed_root(self):
        manager = self._create_user("sp.documents.manager")
        manager.write({
            "group_ids": [Command.link(self.env.ref("documents.group_documents_manager").id)]
        })

        values = self.env["documents.document"].with_user(manager).search_panel_select_range(
            "user_folder_id"
        )["values"]
        values_by_name = {value["display_name"]: value for value in values}

        self.assertEqual(values_by_name["TENENET o.z."]["id"], "COMPANY")
        self.assertIn("Inbox", values_by_name)
        self.assertIn("Finance", values_by_name)

    def test_hr_portal_jpg_documents_move_to_background_folder(self):
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        root_folder = team.document_folder_id
        Document = self.env["documents.document"].sudo()
        jpg = Document.create({
            "name": "183958951.jpg",
            "folder_id": root_folder.id,
            "company_id": team.company_id.id,
            "datas": base64.b64encode(b"\xff\xd8\xff\xe0"),
            "mimetype": "image/jpeg",
        })
        pdf = Document.create({
            "name": "policy.pdf",
            "folder_id": root_folder.id,
            "company_id": team.company_id.id,
            "datas": self._sample_pdf_data(),
            "mimetype": "application/pdf",
        })

        team._sync_hr_portal_background_documents()
        jpg.invalidate_recordset(["folder_id"])
        pdf.invalidate_recordset(["folder_id"])

        self.assertEqual(jpg.folder_id.name, "Pozadia blogov")
        self.assertEqual(jpg.folder_id.folder_id, root_folder)
        self.assertEqual(pdf.folder_id, root_folder)

    def test_hr_portal_imported_jpg_documents_use_background_folder(self):
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(f"{tmpdir}/background.jpg", "wb") as handle:
                handle.write(b"\xff\xd8\xff\xe0")
            document = team._get_or_create_imported_document(
                "https://example.com/background.jpg",
                media_path=tmpdir,
            )

        self.assertTrue(document)
        self.assertEqual(document.folder_id.name, "Pozadia blogov")
        self.assertEqual(document.folder_id.folder_id, team.document_folder_id)

    def test_hr_portal_graph_page_import_is_idempotent(self):
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        export_payload = {
            "value": [{
                "id": "page-1",
                "name": "benefits.aspx",
                "title": "Benefits",
                "webUrl": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/benefits.aspx",
                "lastModifiedDateTime": "2026-01-02T03:04:05Z",
                "createdBy": {"user": {"displayName": "HR Team"}},
                "canvasLayout": {
                    "horizontalSections": [{
                        "columns": [{
                            "webparts": [
                                {
                                    "@odata.type": "#microsoft.graph.standardWebPart",
                                    "webPartType": "cbe7b0a9-3504-44dd-a3a3-0e5cacd07788",
                                    "data": {
                                        "title": "Banner",
                                        "properties": {
                                            "altText": "Coast",
                                            "translateY": 41.5,
                                        },
                                        "serverProcessedContent": {
                                            "imageSources": [
                                                {"key": "imageSource", "value": "https://example.com/banner.jpg"},
                                            ],
                                        },
                                    },
                                },
                                {
                                    "@odata.type": "#microsoft.graph.standardWebPart",
                                    "webPartType": "c4bd7b2f-7b6e-4599-8485-16504575f590",
                                    "data": {
                                        "title": "Hero",
                                        "properties": {
                                            "content": [{"titleHTML": "<h2>Employee survey</h2>"}],
                                        },
                                        "serverProcessedContent": {
                                            "searchablePlainTexts": [
                                                {"key": "content[0].title", "value": "Employee survey"},
                                            ],
                                            "links": [
                                                {"key": "content[0].link", "value": "https://example.com/survey"},
                                            ],
                                            "imageSources": [
                                                {"key": "content[0].image.url", "value": "/sites/HRportal/SiteAssets/survey.jpg"},
                                            ],
                                        },
                                    },
                                },
                                {
                                    "@odata.type": "#microsoft.graph.textWebPart",
                                    "innerHtml": (
                                        "<h2>Vacation</h2><p>Use the HR request flow.</p>"
                                        "<figure class=\"table canvasRteResponsiveTable\">"
                                        "<table><tbody><tr><td>Allowance</td></tr></tbody></table>"
                                        "</figure>"
                                    ),
                                },
                                {
                                    "@odata.type": "#microsoft.graph.standardWebPart",
                                    "title": "Policy file",
                                    "data": {"url": "https://example.com/policy.pdf"},
                                },
                                {
                                    "@odata.type": "#microsoft.graph.standardWebPart",
                                    "webPartType": "d1d91016-032f-456d-98a4-721247c305e8",
                                    "data": {
                                        "title": "Obrázok",
                                        "properties": {"altText": "Team", "fileName": "team.jpg"},
                                        "serverProcessedContent": {
                                            "imageSources": [
                                                {"key": "imageSource", "value": "/sites/HRportal/SiteAssets/team.jpg"},
                                            ],
                                        },
                                    },
                                },
                                {
                                    "@odata.type": "#microsoft.graph.standardWebPart",
                                    "webPartType": "c70391ea-0b10-4ee9-b2b4-006d3fcad0cd",
                                    "data": {
                                        "title": "Rýchle prepojenia",
                                        "properties": {"items": [{"id": 1}]},
                                        "serverProcessedContent": {
                                            "searchablePlainTexts": [
                                                {"key": "title", "value": "Useful links"},
                                                {"key": "items[0].title", "value": "Payroll"},
                                            ],
                                            "links": [
                                                {"key": "items[0].sourceItem.url", "value": "/sites/HRportal/payroll.xlsx"},
                                            ],
                                            "imageSources": [],
                                        },
                                    },
                                },
                            ],
                        }],
                    }, {
                        "layout": "threeColumns",
                        "columns": [
                            {
                                "width": 4,
                                "webparts": [{
                                    "@odata.type": "#microsoft.graph.textWebPart",
                                    "innerHtml": (
                                        "<h3 style=\"margin-left:40px;text-align:justify;\">Referral bonus</h3>"
                                        "<p style=\"margin-left:40px;text-align:justify;\">"
                                        "Long employee benefit paragraph.</p>"
                                    ),
                                }],
                            },
                            {
                                "width": 4,
                                "webparts": [{
                                    "@odata.type": "#microsoft.graph.textWebPart",
                                    "innerHtml": "<p>Second column.</p>",
                                }],
                            },
                        ],
                    }],
                },
            }, {
                "id": "blank-home",
                "name": "Home.aspx",
                "title": "Domov",
                "webUrl": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/Home.aspx",
                "canvasLayout": {"horizontalSections": []},
            }],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            export_file = f"{tmpdir}/pages.json"
            media_file = f"{tmpdir}/policy.pdf"
            survey_image_file = f"{tmpdir}/survey.jpg"
            team_image_file = f"{tmpdir}/team.jpg"
            banner_image_file = f"{tmpdir}/banner.jpg"
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump(export_payload, handle)
            with open(media_file, "wb") as handle:
                handle.write(b"%PDF-1.4\n")
            with open(survey_image_file, "wb") as handle:
                handle.write(b"\xff\xd8\xff\xe0")
            with open(team_image_file, "wb") as handle:
                handle.write(b"\xff\xd8\xff\xe0")
            with open(banner_image_file, "wb") as handle:
                handle.write(b"\xff\xd8\xff\xe0")

            blank_article = self.env["knowledge.article"].sudo().create({
                "name": "Domov",
                "body": "<h1>Domov</h1><p></p>",
                "parent_id": team.knowledge_article_id.id,
                "internal_permission": False,
                "is_desynchronized": False,
                "sharepoint_source_id": "blank-home",
                "sharepoint_source_url": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/Home.aspx",
            })

            stats = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)
            stats_again = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)
            imported_article = self.env["knowledge.article"].sudo().search([
                ("sharepoint_source_id", "=", "page-1"),
            ])
            imported_article.action_send_to_trash()
            stats_reactivated = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)

        article = self.env["knowledge.article"].sudo().search([
            ("sharepoint_source_id", "=", "page-1"),
        ])
        benefits_folder = self.env["documents.document"].sudo().search([
            ("type", "=", "folder"),
            ("folder_id", "=", team.document_folder_id.id),
            ("name", "=", "Benefits"),
        ])
        document = self.env["documents.document"].sudo().search([
            ("folder_id", "=", benefits_folder.id),
            ("name", "=", "policy.pdf"),
        ])

        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats_again["updated"], 1)
        self.assertEqual(stats_again["skipped"], 1)
        self.assertEqual(stats_reactivated["updated"], 1)
        self.assertEqual(stats_reactivated["skipped"], 1)
        self.assertFalse(blank_article.active)
        self.assertTrue(blank_article.to_delete)
        self.assertEqual(len(article), 1)
        self.assertTrue(article.active)
        self.assertFalse(article.to_delete)
        self.assertEqual(article.parent_id, team.knowledge_article_id)
        self.assertIn("Employee survey", article.body)
        self.assertIn("Vacation", article.body)
        self.assertIn("Useful links", article.body)
        self.assertIn("https://tenenetsk.sharepoint.com/sites/HRportal/payroll.xlsx", article.body)
        self.assertIn("/web/image/", article.body)
        self.assertIn("<table", article.body)
        self.assertNotIn("<figure class=\"table", article.body)
        self.assertNotIn("<h3>Banner</h3>", article.body)
        self.assertNotIn("margin-left:40px", article.body)
        self.assertNotIn("text-align:justify", article.body)
        self.assertNotIn("c4bd7b2f", article.body)
        self.assertTrue(article.cover_image_id)
        self.assertIn("banner.jpg", article.cover_image_id.attachment_id.name)
        self.assertEqual(article.cover_image_position, 41.5)
        self.assertEqual(article.sharepoint_source_modified.year, 2026)
        self.assertEqual(article.sharepoint_source_author, "HR Team")
        self.assertEqual(len(benefits_folder), 1)
        self.assertEqual(len(document), 1)

    def test_generic_graph_import_scopes_source_pages_to_target_team(self):
        first_team = self.env["sharepoint.team"].create({"name": "First managed site"})
        second_team = self.env["sharepoint.team"].create({"name": "Second managed site"})
        first_article = self.env["knowledge.article"].sudo().create({
            "name": "Original first-site page",
            "body": "<p>Keep this content.</p>",
            "parent_id": first_team.knowledge_article_id.id,
            "internal_permission": False,
            "is_desynchronized": False,
            "sharepoint_source_id": "shared-source-id",
        })
        payload = {
            "value": [{
                "id": "shared-source-id",
                "name": "second.aspx",
                "title": "Second-site page",
                "description": "Imported into the second team only.",
                "canvasLayout": {"horizontalSections": []},
            }],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            export_file = f"{tmpdir}/pages.json"
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            stats = second_team.import_graph_site_pages(export_file)

        second_article = self.env["knowledge.article"].sudo().search([
            ("parent_id", "=", second_team.knowledge_article_id.id),
            ("sharepoint_source_id", "=", "shared-source-id"),
        ])
        first_article.invalidate_recordset(["name", "body", "parent_id"])
        self.assertEqual(stats["created"], 1)
        self.assertEqual(len(second_article), 1)
        self.assertEqual(second_article.name, "Second-site page")
        self.assertEqual(first_article.name, "Original first-site page")
        self.assertEqual(first_article.parent_id, first_team.knowledge_article_id)
        self.assertIn("Keep this content", first_article.body)

    def test_hr_portal_graph_document_import_groups_files_by_page(self):
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        employee_user = self._create_user("sp.folder.employee")
        employee_user.write({
            "group_ids": [Command.link(self.env.ref("documents.group_documents_user").id)]
        })
        self.env["hr.employee"].create({
            "name": "Folder Employee",
            "user_id": employee_user.id,
            "work_email": employee_user.email,
        })
        team._sync_hr_portal_employee_visitors()

        with tempfile.TemporaryDirectory() as tmpdir:
            export_file = f"{tmpdir}/pages.json"
            payload = self._sharepoint_document_fixture_payload()
            for item in payload["driveItems"]:
                with open(f"{tmpdir}/{item['localFileName']}", "wb") as handle:
                    handle.write(item.pop("content"))
            with open(f"{tmpdir}/banner.jpg", "wb") as handle:
                handle.write(b"\xff\xd8\xff\xe0")
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            stats = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)
            stats_again = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)

        Document = self.env["documents.document"].sudo()
        licencie_folder = Document.search([
            ("type", "=", "folder"),
            ("folder_id", "=", team.document_folder_id.id),
            ("name", "=", "Licencie"),
        ])
        empty_folder = Document.search([
            ("type", "=", "folder"),
            ("folder_id", "=", team.document_folder_id.id),
            ("name", "=", "Bez dokumentov"),
        ])
        policy_documents = Document.search([
            ("name", "=", "policy.pdf"),
            ("type", "!=", "folder"),
            ("folder_id", "=", licencie_folder.id),
        ])
        license_xlsx = Document.search([
            ("name", "=", "licencie.xlsx"),
            ("type", "!=", "folder"),
            ("folder_id", "=", licencie_folder.id),
        ])
        duplicate_policy = Document.search([
            ("name", "=", "policy.pdf"),
            ("sharepoint_drive_item_id", "=", "benefit-policy"),
        ])
        banner = Document.search([
            ("name", "=", "banner.jpg"),
            ("type", "!=", "folder"),
        ], limit=1)
        article = self.env["knowledge.article"].sudo().search([
            ("sharepoint_source_id", "=", "licencie-page"),
        ], limit=1)
        search_panel_values = self.env["documents.document"].with_user(employee_user).search_panel_select_range(
            "user_folder_id"
        )["values"]
        search_panel_names = {value["display_name"] for value in search_panel_values}

        self.assertEqual(stats["documents_created"], 3)
        self.assertGreaterEqual(stats_again["documents_unchanged"], 3)
        self.assertEqual(len(licencie_folder), 1)
        self.assertFalse(empty_folder)
        self.assertEqual(len(policy_documents), 1)
        self.assertEqual(len(license_xlsx), 1)
        self.assertEqual(policy_documents.sharepoint_drive_id, "drive-main")
        self.assertEqual(policy_documents.sharepoint_drive_item_id, "licencie-policy")
        self.assertEqual(policy_documents.sharepoint_source_page_title, "Licencie")
        self.assertIn(policy_documents.access_url, article.body)
        self.assertNotIn("Shared%20Documents/Licencie/policy.pdf", article.body)
        self.assertEqual(len(duplicate_policy), 1)
        self.assertNotEqual(duplicate_policy.folder_id, licencie_folder)
        self.assertEqual(banner.folder_id.name, "Pozadia blogov")
        self.assertIn("Licencie", search_panel_names)

    def test_hr_portal_graph_document_import_updates_content_on_etag_change(self):
        team = self.env["sharepoint.team"].sudo()._get_or_create_hr_portal_team()
        payload = self._sharepoint_document_fixture_payload()
        payload["value"] = payload["value"][:1]
        payload["driveItems"] = payload["driveItems"][:1]

        with tempfile.TemporaryDirectory() as tmpdir:
            export_file = f"{tmpdir}/pages.json"
            item = payload["driveItems"][0]
            with open(f"{tmpdir}/{item['localFileName']}", "wb") as handle:
                handle.write(item.pop("content"))
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)

            item["eTag"] = "etag-policy-2"
            with open(f"{tmpdir}/{item['localFileName']}", "wb") as handle:
                handle.write(b"%PDF-1.4 changed\n")
            with open(export_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            stats = team.action_import_hr_portal_graph_pages(export_file, media_dir=tmpdir)

        document = self.env["documents.document"].sudo().search([
            ("sharepoint_drive_item_id", "=", "licencie-policy"),
        ], limit=1)

        self.assertEqual(stats["documents_updated"], 1)
        self.assertEqual(document.sharepoint_source_etag, "etag-policy-2")
        self.assertIn(b"changed", base64.b64decode(document.datas))

    def _sharepoint_document_fixture_payload(self):
        return {
            "value": [
                {
                    "id": "licencie-page",
                    "name": "Licencie.aspx",
                    "title": "Licencie",
                    "webUrl": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/Licencie.aspx",
                    "canvasLayout": {
                        "horizontalSections": [{
                            "columns": [{
                                "webparts": [
                                    {
                                        "@odata.type": "#microsoft.graph.textWebPart",
                                        "innerHtml": (
                                            "<p><a href=\"/sites/HRportal/Shared%20Documents/"
                                            "Licencie/policy.pdf\">Policy</a></p>"
                                        ),
                                    },
                                    {
                                        "@odata.type": "#microsoft.graph.standardWebPart",
                                        "webPartType": "f92bf067-bc19-489e-a556-7fe95f508720",
                                        "data": {
                                            "title": "Dokumenty",
                                            "properties": {
                                                "selectedListUrl": (
                                                    "/sites/HRportal/Shared%20Documents/Licencie"
                                                ),
                                            },
                                        },
                                    },
                                    {
                                        "@odata.type": "#microsoft.graph.standardWebPart",
                                        "title": "Banner",
                                        "data": {"url": "https://example.com/banner.jpg"},
                                    },
                                ],
                            }],
                        }],
                    },
                },
                {
                    "id": "empty-page",
                    "name": "Bez-dokumentov.aspx",
                    "title": "Bez dokumentov",
                    "webUrl": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/Bez-dokumentov.aspx",
                    "canvasLayout": {
                        "horizontalSections": [{
                            "columns": [{
                                "webparts": [{
                                    "@odata.type": "#microsoft.graph.textWebPart",
                                    "innerHtml": "<p>Bez priloh.</p>",
                                }],
                            }],
                        }],
                    },
                },
                {
                    "id": "benefity-page",
                    "name": "Benefity.aspx",
                    "title": "Benefity",
                    "webUrl": "https://tenenetsk.sharepoint.com/sites/HRportal/SitePages/Benefity.aspx",
                    "canvasLayout": {
                        "horizontalSections": [{
                            "columns": [{
                                "webparts": [{
                                    "@odata.type": "#microsoft.graph.textWebPart",
                                    "innerHtml": "<p>Benefit dokumenty.</p>",
                                }],
                            }],
                        }],
                    },
                },
            ],
            "driveItems": [
                self._sharepoint_drive_item(
                    "licencie-policy",
                    "policy.pdf",
                    "Licencie",
                    "etag-policy-1",
                    "policy-local.pdf",
                    b"%PDF-1.4 original\n",
                ),
                self._sharepoint_drive_item(
                    "licencie-xlsx",
                    "licencie.xlsx",
                    "Licencie",
                    "etag-xlsx-1",
                    "licencie-local.xlsx",
                    b"xlsx original",
                ),
                self._sharepoint_drive_item(
                    "benefit-policy",
                    "policy.pdf",
                    "Benefity",
                    "etag-benefit-1",
                    "benefit-policy-local.pdf",
                    b"%PDF-1.4 benefit\n",
                ),
            ],
        }

    def _sharepoint_drive_item(self, item_id, name, folder, etag, local_filename, content):
        return {
            "id": item_id,
            "driveId": "drive-main",
            "name": name,
            "webUrl": f"https://tenenetsk.sharepoint.com/sites/HRportal/Shared%20Documents/{folder}/{name}",
            "eTag": etag,
            "lastModifiedDateTime": "2026-02-03T04:05:06Z",
            "localFileName": local_filename,
            "content": content,
            "file": {"mimeType": "application/pdf"},
            "parentReference": {
                "driveId": "drive-main",
                "path": f"/drive/root:/Shared Documents/{folder}",
            },
        }

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
