import tempfile
import unittest
from pathlib import Path

from export_graph_site import (
    discover_all_drive_files,
    download_selected_files,
    discover_folder_files,
    synthetic_folder_page,
)


class ExportGraphSiteTest(unittest.TestCase):
    def test_folder_export_selects_only_requested_subtree(self):
        folder_url = (
            "https://tenenetsk.sharepoint.com/sites/VCItm/Zdielane%20dokumenty/"
            "METODIKA%20SVI/SVI%20-%20new/Dokumenty%20SVI"
        )
        page = synthetic_folder_page(folder_url, "SVI")
        items = [
            {
                "id": "inside",
                "name": "metodika.pdf",
                "webUrl": folder_url + "/metodika.pdf",
                "driveId": "drive-svi",
                "file": {"mimeType": "application/pdf"},
            },
            {
                "id": "outside",
                "name": "other.pdf",
                "webUrl": "https://tenenetsk.sharepoint.com/sites/VCItm/Zdielane%20dokumenty/other.pdf",
                "driveId": "drive-svi",
                "file": {"mimeType": "application/pdf"},
            },
        ]

        selected = discover_folder_files(page, items, folder_url)

        self.assertEqual(list(selected), [("drive-svi", "inside")])
        self.assertEqual(selected[("drive-svi", "inside")]["sourcePageIds"], {page["id"]})

    def test_oversize_drive_item_is_kept_as_placeholder(self):
        class NoDownloadClient:
            def download(self, *_args, **_kwargs):
                raise AssertionError("oversize file must not be downloaded")

        selected = {
            ("drive", "large"): {
                "item": {
                    "id": "large",
                    "driveId": "drive",
                    "name": "video.mp4",
                    "webUrl": "https://example.com/video.mp4",
                    "size": 11 * 1024 * 1024,
                    "file": {"mimeType": "video/mp4"},
                },
                "sourcePageIds": {"page"},
                "discoveryReasons": {"document_library"},
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            items = download_selected_files(NoDownloadClient(), selected, Path(tmpdir))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["downloadSkipped"], "size_limit")
        self.assertTrue(items[0]["placeholder"])
        self.assertNotIn("localFileName", items[0])

    def test_all_drive_file_export_selects_every_file_but_not_folders(self):
        page = synthetic_folder_page("https://example.com/sites/KomTeam/Documents", "Dokumenty")
        items = [
            {"id": "folder", "name": "FOTKY", "folder": {}, "driveId": "drive"},
            {"id": "photo", "name": "cover.png", "file": {}, "driveId": "drive"},
            {"id": "document", "name": "brief.pdf", "file": {}, "driveId": "drive"},
        ]

        selected = discover_all_drive_files(page, items)

        self.assertEqual(set(selected), {("drive", "photo"), ("drive", "document")})
        self.assertEqual(selected[("drive", "document")]["sourcePageIds"], {page["id"]})
        self.assertEqual(selected[("drive", "document")]["discoveryReasons"], {"all_drive_files"})


if __name__ == "__main__":
    unittest.main()
