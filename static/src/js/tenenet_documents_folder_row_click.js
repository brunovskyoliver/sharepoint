import { patch } from "@web/core/utils/patch";
import { DocumentsKanbanRecord } from "@documents/views/kanban/documents_kanban_record";
import { DocumentsListRenderer } from "@documents/views/list/documents_list_renderer";

export const TENENET_OPEN_ONLYOFFICE = "tenenet-open-onlyoffice";

const INTERACTIVE_TARGETS = [
    ".dropdown",
    ".o_documents_list_actions",
    ".o_list_record_selector",
    ".o_field_document_favorite",
    ".oe_kanban_action",
    "a",
    "button",
    "input",
    "textarea",
    "select",
].join(",");

function hasModifier(ev) {
    return ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey;
}

export function openDocument(record, ev) {
    const target = record.shortcutTarget || record;
    if (record.data.type === "folder" || target.data.type === "folder") {
        record.openFolder();
        return;
    }
    if (record.isRequest?.()) {
        return;
    }
    if (record.isURL?.() || record.isViewable?.()) {
        return record.onClickPreview(ev);
    }
    record.model.env.documentsView.bus.trigger(TENENET_OPEN_ONLYOFFICE, { record: target });
}

patch(DocumentsListRenderer.prototype, {
    onCellClicked(record, column, ev) {
        const isInteractiveTarget = ev.target.closest(INTERACTIVE_TARGETS);
        const opensOnSingleClick =
            column.name === "name" || ev.target.closest(".o_field_documents_type_icon");

        if (!hasModifier(ev) && !isInteractiveTarget && opensOnSingleClick) {
            if (ev.detail > 1) {
                return;
            }
            ev.stopPropagation();
            return openDocument(record, ev);
        }
        if (!hasModifier(ev) && !isInteractiveTarget && ev.detail > 1) {
            ev.stopPropagation();
            return openDocument(record, ev);
        }
        return super.onCellClicked(...arguments);
    },

    onGlobalKeydown(ev) {
        if (ev.key !== "Enter" || this.editedRecord) {
            return super.onGlobalKeydown(...arguments);
        }
        const row = ev.target.closest(".o_data_row");
        const record = row && this.props.list.records.find((item) => item.id === row.dataset.id);
        if (!record) {
            return super.onGlobalKeydown(...arguments);
        }
        ev.stopPropagation();
        ev.preventDefault();
        return openDocument(record, ev);
    },
});

patch(DocumentsKanbanRecord.prototype, {
    onGlobalClick(ev) {
        const record = this.props.record;
        const isInteractiveTarget = ev.target.closest(INTERACTIVE_TARGETS);
        const opensOnSingleClick = ev.target.closest("[name='document_preview'], [name='name']");

        if (!hasModifier(ev) && !isInteractiveTarget && opensOnSingleClick) {
            if (ev.detail > 1) {
                return;
            }
            ev.stopPropagation();
            return openDocument(record, ev);
        }
        if (!hasModifier(ev) && !isInteractiveTarget && ev.detail > 1) {
            ev.stopPropagation();
            return openDocument(record, ev);
        }
        if (!hasModifier(ev) && !isInteractiveTarget && record.data.type === "folder") {
            this.props
                .getSelection()
                .forEach((selectedRecord) => selectedRecord.toggleSelection(false));
            this.rootRef.el.focus();
            this.props.toggleSelection(record);
            return;
        }
        return super.onGlobalClick(...arguments);
    },
});
