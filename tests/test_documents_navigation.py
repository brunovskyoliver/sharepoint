from odoo.tests import TransactionCase


class TestDocumentsNavigation(TransactionCase):

    def test_documents_is_the_only_documents_and_knowledge_app(self):
        documents_root = self.env.ref("documents.menu_root")
        documents_menu = self.env.ref("documents.dashboard")
        teams_menu = self.env.ref("sharepoint.menu_sharepoint_teams")
        articles_menu = self.env.ref("knowledge.knowledge_menu_article")
        configuration_menu = self.env.ref("documents.Config")

        self.assertEqual(documents_root.name, "Dokumenty")
        self.assertEqual(documents_menu.parent_id, documents_root)
        self.assertEqual(
            documents_menu.action,
            self.env.ref("documents.document_action_preference"),
        )
        self.assertEqual(teams_menu.parent_id, documents_root)
        self.assertEqual(articles_menu.parent_id, documents_root)
        self.assertEqual(articles_menu.name, "Články")
        self.assertEqual(
            articles_menu.action,
            self.env.ref("knowledge.ir_actions_server_knowledge_home_page"),
        )
        self.assertEqual(configuration_menu.parent_id, documents_root)
        menus = (teams_menu, documents_menu, articles_menu, configuration_menu)
        self.assertEqual(
            [menu.id for menu in menus],
            [menu.id for menu in sorted(menus, key=lambda menu: menu.sequence)],
        )
        self.assertFalse(self.env.ref("knowledge.knowledge_menu_root").active)
        self.assertFalse(self.env.ref("knowledge.knowledge_menu_home").active)

    def test_knowledge_configuration_is_merged_into_documents_configuration(self):
        configuration_menu = self.env.ref("documents.Config")

        self.assertFalse(self.env.ref("knowledge.knowledge_menu_configuration").active)
        for xmlid in (
            "knowledge.knowledge_article_member_menu",
            "knowledge.knowledge_article_favorite_menu",
            "knowledge.knowledge_article_menu_trashed",
            "knowledge.knowledge_article_stage_menu",
        ):
            with self.subTest(menu=xmlid):
                self.assertEqual(self.env.ref(xmlid).parent_id, configuration_menu)
