import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

const DOCUMENTS_FOLDER_STORAGE_KEY = "searchpanel_documents_document";

export function getDocumentsActionContext(action, options = {}) {
    const additionalContext = options.additionalContext || {};
    const context = {
        ...action.context,
        ...additionalContext,
    };
    const hasSpecificTarget =
        "searchpanel_default_user_folder_id" in context ||
        "documents_unique_folder_id" in context ||
        "documents_init_document_id" in context;

    const hasPersistedFolder = Boolean(
        browser.localStorage.getItem(DOCUMENTS_FOLDER_STORAGE_KEY)
    );
    const hasExplicitInitialFolder = "documents_init_folder_id" in additionalContext;

    if (
        context.documents_init_folder_id === "MY" &&
        (hasSpecificTarget || (hasPersistedFolder && !hasExplicitInitialFolder))
    ) {
        delete context.documents_init_folder_id;
    } else if (!("documents_init_folder_id" in context) && !hasSpecificTarget) {
        context.documents_init_folder_id = "MY";
    }
    return context;
}

export function getDocumentsActionViewType(options = {}) {
    return options.viewType || browser.localStorage.getItem("documentsDefaultViewType") || "list";
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
            viewType: getDocumentsActionViewType(options),
        }
    );
}

registry.category("actions").add("document_action_preference", documentActionPreference, {
    force: true,
});
