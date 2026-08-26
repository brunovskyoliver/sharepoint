import { afterEach, beforeEach, describe, expect, test } from "@odoo/hoot";
import { browser } from "@web/core/browser/browser";
import {
    getDocumentsActionContext,
    getDocumentsActionViewType,
} from "@sharepoint/js/tenenet_documents_action_preference";
import {
    openDocument,
    TENENET_OPEN_ONLYOFFICE,
} from "@sharepoint/js/tenenet_documents_folder_row_click";
import { supportsAction } from "@sharepoint/js/tenenet_documents_onlyoffice_bridge";

const STORAGE_KEYS = ["searchpanel_documents_document", "documentsDefaultViewType"];
let previousStorage;

beforeEach(() => {
    previousStorage = Object.fromEntries(
        STORAGE_KEYS.map((key) => [key, browser.localStorage.getItem(key)])
    );
    STORAGE_KEYS.forEach((key) => browser.localStorage.removeItem(key));
});

afterEach(() => {
    STORAGE_KEYS.forEach((key) => {
        const previousValue = previousStorage[key];
        if (previousValue === null) {
            browser.localStorage.removeItem(key);
        } else {
            browser.localStorage.setItem(key, previousValue);
        }
    });
});

describe("TENENET Documents interactions", () => {

test("defaults to My Drive without overriding explicit targets", () => {
    expect(getDocumentsActionContext({ context: {} })).toEqual({
        documents_init_folder_id: "MY",
    });
    expect(
        getDocumentsActionContext({
            context: {
                documents_init_folder_id: "MY",
                documents_unique_folder_id: 42,
            },
        })
    ).toEqual({ documents_unique_folder_id: 42 });
    expect(
        getDocumentsActionContext({
            context: { documents_init_folder_id: 42 },
        })
    ).toEqual({ documents_init_folder_id: 42 });
    expect(
        getDocumentsActionContext({
            context: { documents_init_folder_id: "COMPANY" },
        })
    ).toEqual({ documents_init_folder_id: "COMPANY" });
});

test("restores the persisted folder instead of the My Drive default", () => {
    browser.localStorage.setItem("searchpanel_documents_document", "42");

    expect(
        getDocumentsActionContext({ context: { documents_init_folder_id: "MY" } })
    ).toEqual({});
});

test("keeps an explicit initial folder over the persisted folder", () => {
    browser.localStorage.setItem("searchpanel_documents_document", "42");

    expect(
        getDocumentsActionContext(
            { context: { documents_init_folder_id: "MY" } },
            { additionalContext: { documents_init_folder_id: "MY" } }
        )
    ).toEqual({ documents_init_folder_id: "MY" });
});

test("restores the persisted view mode", () => {
    browser.localStorage.setItem("documentsDefaultViewType", "kanban");

    expect(getDocumentsActionViewType()).toBe("kanban");
    expect(getDocumentsActionViewType({ viewType: "activity" })).toBe("activity");
});

test("uses native folder, URL, and preview open paths", () => {
    const event = {};
    let opened;
    const record = {
        data: { type: "folder" },
        openFolder: () => (opened = "folder"),
    };
    openDocument(record, event);
    expect(opened).toBe("folder");

    Object.assign(record, {
        data: { type: "url" },
        isRequest: () => false,
        isURL: () => true,
        isViewable: () => false,
        onClickPreview: (ev) => (opened = ev),
    });
    openDocument(record, event);
    expect(opened).toBe(event);

    Object.assign(record, {
        data: { type: "binary" },
        isURL: () => false,
        isViewable: () => true,
    });
    openDocument(record, event);
    expect(opened).toBe(event);
});

test("routes non-previewable attached files to ONLYOFFICE", () => {
    let triggered;
    const target = { data: { id: 7, type: "binary", attachment_id: { id: 9 } } };
    const record = {
        data: { type: "binary" },
        isRequest: () => false,
        isURL: () => false,
        isViewable: () => false,
        shortcutTarget: target,
        model: {
            env: {
                documentsView: {
                    bus: {
                        trigger: (name, detail) => (triggered = { name, detail }),
                    },
                },
            },
        },
    };

    openDocument(record, {});

    expect(triggered).toEqual({
        name: TENENET_OPEN_ONLYOFFICE,
        detail: { record: target },
    });
});

test("ONLYOFFICE format checks are safe when manifest loading fails", () => {
    expect(supportsAction(undefined, "docx", ["edit"])).toBe(false);
    expect(supportsAction([], undefined, ["edit"])).toBe(false);
    expect(
        supportsAction(
            [{ name: "docx", actions: ["view", "edit"] }],
            "DOCX",
            ["edit"]
        )
    ).toBe(true);
    expect(
        supportsAction([{ name: "pdf", actions: ["view"] }], "pdf", ["edit"])
    ).toBe(false);
});

});
