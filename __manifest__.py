{
    "name": "SharePoint",
    "summary": "Jednotné tímové centrum pre Odoo Dokumenty, Znalosti a OnlyOffice",
    "description": """
Tímové priestory v štýle SharePointu pre Tenenet.

Vytvára tímové knižnice dokumentov a stránky v Znalostiach, pričom zachováva
natívne Odoo Dokumenty, Znalosti, HR priečinky dokumentov a úpravy v OnlyOffice.
    """,
    "author": "Tenenet",
    "website": "https://www.tenenet.sk",
    "category": "Produktivita",
    "version": "19.0.1.11.0",
    "license": "LGPL-3",
    "depends": [
        "documents",
        "documents_hr",
        "knowledge",
        "website_helpdesk_knowledge",
        "onlyoffice_odoo_documents",
        "tenenet_projects",
    ],
    "data": [
        "security/sharepoint_security.xml",
        "security/ir.model.access.csv",
        "data/sharepoint_knowledge_cleanup.xml",
        "data/sharepoint_hr_portal_cron.xml",
        "views/sharepoint_team_views.xml",
        "views/documents_views.xml",
        "views/tenenet_employee_evaluation_views.xml",
        "views/sharepoint_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            ("after", "documents/static/src/core/document_client_action.js", "sharepoint/static/src/js/tenenet_documents_action_preference.js"),
            ("after", "documents/static/src/views/list/documents_list_renderer.js", "sharepoint/static/src/js/tenenet_documents_folder_row_click.js"),
            ("after", "onlyoffice_odoo_documents/static/src/documents_view/kanban/documents_kanban_controller.js", "sharepoint/static/src/js/tenenet_documents_onlyoffice_bridge.js"),
            ("after", "documents/static/src/views/search/documents_search_panel.js", "sharepoint/static/src/js/tenenet_documents_search_panel_guard.js"),
            ("after", "web/static/src/views/kanban/kanban_record.js", "sharepoint/static/src/js/sharepoint_team_kanban.js"),
            "sharepoint/static/src/scss/sharepoint.scss",
        ],
        "web.assets_unit_tests": [
            "sharepoint/static/tests/documents_interaction.test.js",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
