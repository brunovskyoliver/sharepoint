import { registry } from "@web/core/registry";

export function getDocumentsActionContext(action, options = {}) {
    const context = {
        ...action.context,
        ...(options.additionalContext || {}),
    };
    const hasSpecificTarget =
        "searchpanel_default_user_folder_id" in context ||
        "documents_unique_folder_id" in context ||
        "documents_init_document_id" in context;

    if (hasSpecificTarget && context.documents_init_folder_id === "MY") {
        delete context.documents_init_folder_id;
    } else if (!("documents_init_folder_id" in context) && !hasSpecificTarget) {
        context.documents_init_folder_id = "MY";
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
