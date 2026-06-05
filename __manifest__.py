{
    "name": "SharePoint",
    "summary": "Unified team hub for Odoo Documents, Knowledge, and OnlyOffice",
    "description": """
SharePoint-style team spaces for Tenenet.

Creates team-owned document libraries and Knowledge pages while preserving
native Odoo Documents, Knowledge, HR document folders, and OnlyOffice editing.
    """,
    "author": "Tenenet",
    "website": "https://www.tenenet.sk",
    "category": "Productivity",
    "version": "19.0.1.1.0",
    "license": "LGPL-3",
    "depends": [
        "documents",
        "documents_hr",
        "knowledge",
        "onlyoffice_odoo_documents",
        "tenenet_projects",
    ],
    "data": [
        "security/sharepoint_security.xml",
        "security/ir.model.access.csv",
        "views/sharepoint_team_views.xml",
        "views/documents_views.xml",
        "views/tenenet_employee_evaluation_views.xml",
        "views/sharepoint_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            ("after", "documents/static/src/core/document_client_action.js", "sharepoint/static/src/js/tenenet_documents_action_preference.js"),
            ("after", "documents/static/src/views/list/documents_list_renderer.js", "sharepoint/static/src/js/tenenet_documents_folder_row_click.js"),
            ("after", "web/static/src/views/kanban/kanban_record.js", "sharepoint/static/src/js/sharepoint_team_kanban.js"),
            "sharepoint/static/src/scss/sharepoint.scss",
        ],
    },
    "installable": True,
    "application": True,
}
