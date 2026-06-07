import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { registry } from "@web/core/registry";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanRecord } from "@web/views/kanban/kanban_record";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { Component, xml } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

const DIALOG_ROLES = new Set(["member", "visitor"]);

class SharePointTeamDialog extends Component {
    static template = xml`
        <Dialog title="props.teamName" size="'sm'" footer="false">
            <div class="d-flex flex-column gap-2 py-2">
                <button class="btn btn-primary text-start" t-on-click="onDocuments">
                    <i class="fa fa-folder-open me-2"/><t t-out="documentsLabel"/>
                </button>
                <button class="btn btn-secondary text-start" t-on-click="onPages">
                    <i class="fa fa-book me-2"/><t t-out="pagesLabel"/>
                </button>
            </div>
        </Dialog>
    `;
    static components = { Dialog };
    static props = {
        teamName: String,
        onDocuments: Function,
        onPages: Function,
        close: Function,
    };

    get documentsLabel() {
        return _t("Dokumenty");
    }

    get pagesLabel() {
        return _t("Stránky");
    }

    onDocuments() {
        this.props.onDocuments();
        this.props.close();
    }

    onPages() {
        this.props.onPages();
        this.props.close();
    }
}

class SharePointTeamKanbanRecord extends KanbanRecord {
    setup() {
        super.setup();
        this.orm = useService("orm");
        this.spDialogService = useService("dialog");
        this.spActionService = useService("action");
    }

    onGlobalClick(ev) {
        const userRole = this.props.record.data.user_role;
        if (DIALOG_ROLES.has(userRole)) {
            ev.stopPropagation();
            ev.preventDefault();
            const id = this.props.record.resId;
            const name = this.props.record.data.name;
            this.spDialogService.add(SharePointTeamDialog, {
                teamName: name,
                onDocuments: async () => {
                    const act = await this.orm.call(
                        "sharepoint.team",
                        "action_open_documents",
                        [id]
                    );
                    this.spActionService.doAction(act);
                },
                onPages: async () => {
                    const act = await this.orm.call(
                        "sharepoint.team",
                        "action_open_pages",
                        [id]
                    );
                    this.spActionService.doAction(act);
                },
            });
            return;
        }
        super.onGlobalClick(ev);
    }
}

class SharePointTeamKanbanRenderer extends KanbanRenderer {
    static components = {
        ...KanbanRenderer.components,
        KanbanRecord: SharePointTeamKanbanRecord,
    };
}

registry.category("views").add("sharepoint_team_kanban", {
    ...kanbanView,
    Renderer: SharePointTeamKanbanRenderer,
});
