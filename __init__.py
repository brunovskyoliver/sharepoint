from . import models


def post_init_hook(env):
    env["documents.document"]._sharepoint_ensure_documents_user_defaults()
    env["documents.document"]._sharepoint_localize_generated_document_names()
    env["sharepoint.team"]._sync_hr_portal_employee_visitors_all()
