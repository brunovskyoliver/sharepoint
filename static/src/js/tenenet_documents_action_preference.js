import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

function getDocumentsActionContext(action, options = {}, isDocumentsManager = false) {
    const explicitContext = options.additionalContext || {};
    const context = {
        ...action.context,
        ...explicitContext,
    };
    const hasExplicitFolder =
        "searchpanel_default_user_folder_id" in context ||
        "documents_unique_folder_id" in context ||
        "documents_init_document_id" in context;

    if (context.documents_init_folder_id === "COMPANY" && hasExplicitFolder) {
        delete context.documents_init_folder_id;
    } else if (context.documents_init_folder_id === "COMPANY" && !isDocumentsManager) {
        context.documents_init_folder_id = "MY";
    }
    return context;
}

async function documentActionPreference(env, action, options = {}) {
    const nextAction = await env.services.action.loadAction("documents.document_action");
    const isDocumentsManager = await user.hasGroup("documents.group_documents_manager");

    return env.services.action.doAction(
        {
            ...nextAction,
            context: getDocumentsActionContext(action, options, isDocumentsManager),
            domain: action.domain,
        },
        {
            ...options,
            viewType: "list",
        }
    );
}

registry.category("actions").add("document_action_preference", documentActionPreference, {
    force: true,
});
