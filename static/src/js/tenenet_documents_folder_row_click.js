import { patch } from "@web/core/utils/patch";
import { DocumentsListRenderer } from "@documents/views/list/documents_list_renderer";

patch(DocumentsListRenderer.prototype, {
    onCellClicked(record, column, ev) {
        if (
            record.data.type === "folder" &&
            !ev.ctrlKey &&
            !ev.metaKey &&
            !ev.shiftKey &&
            !ev.altKey &&
            !ev.target.closest(".o_documents_list_actions")
        ) {
            ev.stopPropagation();
            record.openFolder();
            return;
        }

        return super.onCellClicked(...arguments);
    },
});
