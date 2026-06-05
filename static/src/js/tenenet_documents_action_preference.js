import { registry } from "@web/core/registry";

function getDocumentsActionContext(action, options = {}) {
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
    }
    return context;
}

async function documentActionPreference(env, action, options = {}) {
    const nextAction = await env.services.action.loadAction("documents.document_action");

    return env.services.action.doAction(
        {
            ...nextAction,
            context: getDocumentsActionContext(action, options),
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
