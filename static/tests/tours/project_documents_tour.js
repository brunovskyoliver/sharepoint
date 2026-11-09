import { registry } from "@web/core/registry";

const openDocuments = [
    {
        content: "Open the project's documents",
        trigger: ".o_form_view button[name=action_open_project_documents]",
        run: "click",
    },
    {
        content: "The project folder is shown with its file",
        trigger: ".o_documents_content :contains('zmluva-tour.txt')",
    },
];

registry.category("web_tour.tours").add("sharepoint_project_documents_tour", {
    steps: () => [
        ...openDocuments,
        {
            content: "Breadcrumb leads back to the project",
            trigger: ".o_breadcrumb a:contains('Tour projekt PD')",
            run: "click",
        },
        ...openDocuments,
        {
            content: "Browser back returns to the project",
            trigger: ".o_documents_content",
            run() {
                window.history.back();
            },
        },
        {
            content: "Project form is back",
            trigger: ".o_form_view button[name=action_open_project_documents]",
        },
    ],
});
