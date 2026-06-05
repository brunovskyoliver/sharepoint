import { patch } from "@web/core/utils/patch";
import { DocumentsListRenderer } from "@documents/views/list/documents_list_renderer";

patch(DocumentsListRenderer.prototype, {
    onCellClicked(record, column, ev) {
        const hasModifier = ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey;
        const isInteractiveTarget = ev.target.closest(
            [
                ".o_documents_list_actions",
                ".o_list_record_selector",
                ".o_field_document_favorite",
                "a",
                "button",
                "input",
                "textarea",
                "select",
            ].join(",")
        );

        if (
            !hasModifier &&
            !isInteractiveTarget &&
            record.data.type === "folder"
        ) {
            ev.stopPropagation();
            record.openFolder();
            return;
        }

        if (
            !hasModifier &&
            !isInteractiveTarget &&
            record.data.type !== "folder" &&
            !record.isRequest?.() &&
            (record.isViewable?.() || record.isURL?.())
        ) {
            return record.onClickPreview(ev);
        }

        return super.onCellClicked(...arguments);
    },
});
