import { DocumentsKanbanController } from "@documents/views/kanban/documents_kanban_controller";
import { DocumentsListController } from "@documents/views/list/documents_list_controller";
import {
    CreateModeDialog,
} from "@onlyoffice_odoo_documents/documents_view/create_mode_dialog/create_mode_dialog";
import { useBus, useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { TENENET_OPEN_ONLYOFFICE } from "./tenenet_documents_folder_row_click";

export function supportsAction(formats, extension, actions) {
    if (!Array.isArray(formats) || !extension) {
        return false;
    }
    const format = formats.find((item) => item.name === extension.toLowerCase());
    return Boolean(format?.actions?.some((action) => actions.includes(action)));
}

function onlyofficeControllerPatch() {
    return {
        setup() {
            super.setup(...arguments);
            this.actionService ||= this.action;
            useBus(this.env.documentsView.bus, TENENET_OPEN_ONLYOFFICE, ({ detail }) => {
                const record = detail.record;
                if (
                    !this.documentService.userIsInternal ||
                    record.data.type === "folder" ||
                    record.isRequest?.() ||
                    !record.data.attachment_id ||
                    !this.showOnlyofficeButton(record)
                ) {
                    return;
                }
                return this.onlyofficeEditorUrl(record);
            });
        },

        onlyofficeCanEdit(extension) {
            return supportsAction(this.formats, extension, ["edit"]);
        },

        onlyofficeCanView(extension) {
            return supportsAction(this.formats, extension, ["view", "edit"]);
        },
    };
}

patch(DocumentsListController.prototype, onlyofficeControllerPatch());
patch(DocumentsKanbanController.prototype, onlyofficeControllerPatch());

patch(CreateModeDialog.prototype, {
    setup() {
        super.setup(...arguments);
        this.action = useService("action");
    },
});
