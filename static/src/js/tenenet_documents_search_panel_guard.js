import { DocumentsSearchPanel } from "@documents/views/search/documents_search_panel";
import { patch } from "@web/core/utils/patch";

patch(DocumentsSearchPanel.prototype, {
    _expandFolder({ folderId }) {
        if (folderId && !this.env.searchModel.getFolderById(folderId)) {
            return;
        }
        return super._expandFolder(...arguments);
    },
});
