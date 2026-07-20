#!/usr/bin/env python3
"""Export SharePoint site pages and referenced drive files for Odoo import.

Authentication intentionally accepts only an OAuth access token from
GRAPH_ACCESS_TOKEN or a token command. Do not put passwords in this script.
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
DOCUMENT_LIBRARY_WEBPART = "f92bf067-bc19-489e-a556-7fe95f508720"
BANNER_WEBPART = "cbe7b0a9-3504-44dd-a3a3-0e5cacd07788"
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024


class GraphClient:
    def __init__(self, token):
        self.token = token

    def request(self, url):
        target = url if url.startswith("https://") else GRAPH_ROOT + url
        return Request(target, headers={"Authorization": f"Bearer {self.token}"})

    def get_json(self, url):
        with urlopen(self.request(url)) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_all(self, url):
        values = []
        next_url = url
        while next_url:
            payload = self.get_json(next_url)
            values.extend(payload.get("value") or [])
            next_url = payload.get("@odata.nextLink")
        return values

    def download(self, url, target, max_bytes=MAX_DOWNLOAD_BYTES):
        with urlopen(self.request(url)) as response:
            content_length = response.headers.get("Content-Length")
            size = int(content_length) if content_length and content_length.isdigit() else None
            if size is not None and size > max_bytes:
                return {"downloaded": False, "size": size}
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                return {"downloaded": False, "size": len(content)}
            target.write_bytes(content)
            return {"downloaded": True, "size": size or len(content)}


def token_from_args(args):
    if args.token_command:
        return subprocess.check_output(args.token_command, shell=True, text=True).strip()
    token = os.environ.get("GRAPH_ACCESS_TOKEN")
    if token:
        return token
    raise SystemExit("Set GRAPH_ACCESS_TOKEN or pass --token-command.")


def iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_strings(child)


def iter_webparts(page):
    layout = page.get("canvasLayout") or {}
    for section in layout.get("horizontalSections") or []:
        for column in section.get("columns") or []:
            for webpart in column.get("webparts") or []:
                if isinstance(webpart, dict):
                    yield webpart
    for webpart in (layout.get("verticalSection") or {}).get("webparts") or []:
        if isinstance(webpart, dict):
            yield webpart


def absolute_url(value, page):
    if not value or not isinstance(value, str):
        return None
    if value.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return value
    if value.startswith("/"):
        parsed = urlsplit(page.get("webUrl") or "")
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        return origin + value
    return value


def looks_like_file_url(value, image=None):
    if not value or not isinstance(value, str):
        return False
    clean = value.split("?", 1)[0].lower()
    image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
    file_extensions = image_extensions + (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")
    if image is True:
        return clean.endswith(image_extensions)
    if image is False:
        return clean.endswith(file_extensions) and not clean.endswith(image_extensions)
    return clean.endswith(file_extensions)


def path_key(url):
    if not url:
        return ""
    clean = url.split("?", 1)[0]
    parsed = urlsplit(clean)
    path = parsed.path or clean
    return unquote(path).rstrip("/").lower()


def url_key(url):
    if not url:
        return ""
    parsed = urlsplit(url)
    host = (parsed.netloc or "").lower()
    path = path_key(url)
    return f"{host}{path}" if host else path


def normalize_name(value):
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


def item_urls(item):
    return [
        url
        for url in (
            item.get("webUrl"),
            item.get("webDavUrl"),
            item.get("@microsoft.graph.downloadUrl"),
            item.get("sourceUrl"),
        )
        if url
    ]


def item_path(item):
    parent = item.get("parentReference") or {}
    parent_path = parent.get("path") or ""
    if ":" in parent_path:
        parent_path = parent_path.split(":", 1)[1]
    return unquote("/".join(part for part in (parent_path.strip("/"), item.get("name")) if part))


def local_filename_for_url(url):
    name = unquote(Path(urlsplit(url.split("?", 1)[0]).path).name)
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"{digest}_{name}"


def slim_drive_item(item):
    keep = {
        "id",
        "name",
        "webUrl",
        "webDavUrl",
        "eTag",
        "cTag",
        "size",
        "file",
        "folder",
        "lastModifiedDateTime",
        "createdDateTime",
        "parentReference",
        "fileSystemInfo",
        "@microsoft.graph.downloadUrl",
    }
    result = {key: value for key, value in item.items() if key in keep}
    parent = result.setdefault("parentReference", {})
    result["driveId"] = item.get("driveId") or parent.get("driveId")
    result["sourcePath"] = item_path(result)
    return result


def collect_drive_items(client, drive_id, folder_id="root"):
    endpoint = f"/drives/{drive_id}/{folder_id}/children" if folder_id == "root" else f"/drives/{drive_id}/items/{folder_id}/children"
    items = []
    for item in client.get_all(endpoint):
        item.setdefault("driveId", drive_id)
        items.append(item)
        if item.get("folder"):
            items.extend(collect_drive_items(client, drive_id, item["id"]))
    return items


def explicit_urls_for_page(page):
    urls = set()
    for webpart in iter_webparts(page):
        if webpart.get("webPartType") == BANNER_WEBPART:
            continue
        for match in re.finditer(r"""(?:href|src)=["']([^"']+)["']""", webpart.get("innerHtml") or "", re.I):
            value = absolute_url(match.group(1), page)
            if looks_like_file_url(value):
                urls.add(value)
        for value in iter_strings(webpart):
            if looks_like_file_url(value):
                urls.add(absolute_url(value, page))
    return {url for url in urls if url}


def document_library_urls_for_page(page):
    urls = set()
    for webpart in iter_webparts(page):
        if webpart.get("webPartType") != DOCUMENT_LIBRARY_WEBPART:
            continue
        properties = ((webpart.get("data") or {}).get("properties") or {})
        url = absolute_url(properties.get("selectedListUrl"), page)
        if url:
            urls.add(url)
    return urls


def discover_files(pages, drive_items):
    item_by_url = {}
    for item in drive_items:
        if item.get("folder"):
            continue
        for url in item_urls(item):
            item_by_url[url_key(url)] = item

    selected = {}
    external_urls = {}

    def select(item, page, reason):
        if item.get("folder"):
            return
        key = (item.get("driveId") or (item.get("parentReference") or {}).get("driveId"), item.get("id"))
        details = selected.setdefault(key, {"item": item, "sourcePageIds": set(), "discoveryReasons": set()})
        details["sourcePageIds"].add(page.get("id") or page.get("webUrl") or page.get("name"))
        details["discoveryReasons"].add(reason)

    for page in pages:
        for url in explicit_urls_for_page(page):
            item = item_by_url.get(url_key(url))
            if item:
                select(item, page, "explicit")
            else:
                external_urls[url] = page.get("id") or page.get("webUrl") or page.get("name")

        library_paths = {path_key(url) for url in document_library_urls_for_page(page)}
        for item in drive_items:
            if item.get("folder"):
                continue
            paths = {path_key(url) for url in item_urls(item)}
            paths.add(path_key(item_path(item)))
            if any(path and library and path.startswith(library.rstrip("/") + "/") for path in paths for library in library_paths):
                select(item, page, "document_library")

        page_names = {
            normalize_name(page.get("title")),
            normalize_name(Path(page.get("name") or "").stem),
            normalize_name(Path(urlsplit(page.get("webUrl") or "").path).stem),
        }
        page_names.discard("")
        for item in drive_items:
            if item.get("folder") or looks_like_file_url(item.get("name"), image=True):
                continue
            parent_parts = [normalize_name(part) for part in item_path(item).split("/")[:-1]]
            if page_names & set(parent_parts):
                select(item, page, "page_folder")

    return selected, external_urls


def discover_folder_files(page, drive_items, folder_url):
    folder_path = path_key(folder_url).rstrip("/")
    selected = {}
    for item in drive_items:
        if item.get("folder"):
            continue
        paths = {path_key(url) for url in item_urls(item)}
        if not any(path == folder_path or path.startswith(folder_path + "/") for path in paths):
            continue
        key = (item.get("driveId") or (item.get("parentReference") or {}).get("driveId"), item.get("id"))
        selected[key] = {
            "item": item,
            "sourcePageIds": {page["id"]},
            "discoveryReasons": {"requested_folder"},
        }
    return selected


def synthetic_folder_page(folder_url, title):
    return {
        "id": f"folder:{url_key(folder_url)}",
        "name": title,
        "title": title,
        "webUrl": folder_url,
        "description": f"Dokumenty importované zo SharePoint priečinka {title}.",
        "canvasLayout": {
            "horizontalSections": [{
                "columns": [{
                    "width": 12,
                    "webparts": [{
                        "@odata.type": "#microsoft.graph.standardWebPart",
                        "webPartType": DOCUMENT_LIBRARY_WEBPART,
                        "data": {
                            "title": title,
                            "properties": {"selectedListUrl": folder_url},
                        },
                    }],
                }],
            }],
        },
    }


def download_selected_files(client, selected, media_dir, max_bytes=MAX_DOWNLOAD_BYTES):
    media_dir.mkdir(parents=True, exist_ok=True)
    exported = []
    for details in selected.values():
        item = slim_drive_item(details["item"])
        source_url = item.get("webUrl") or item.get("@microsoft.graph.downloadUrl") or item["name"]
        local_name = local_filename_for_url(source_url)
        target = media_dir / local_name
        size = item.get("size")
        result = {"downloaded": False, "size": size}
        if not size or size <= max_bytes:
            if target.exists() and target.stat().st_size <= max_bytes:
                result = {"downloaded": True, "size": target.stat().st_size}
            else:
                drive_id = item.get("driveId") or (item.get("parentReference") or {}).get("driveId")
                result = client.download(
                    f"/drives/{drive_id}/items/{item['id']}/content",
                    target,
                    max_bytes=max_bytes,
                )
        if result["downloaded"]:
            item["localFileName"] = local_name
        else:
            item.update({
                "downloadSkipped": "size_limit",
                "downloadLimitBytes": max_bytes,
                "placeholder": True,
                "size": result.get("size") or size,
            })
        item["sourcePageIds"] = sorted(details["sourcePageIds"])
        item["discoveryReasons"] = sorted(details["discoveryReasons"])
        exported.append(item)
    return exported


def download_external_urls(client, external_urls, media_dir, max_bytes=MAX_DOWNLOAD_BYTES):
    exported = []
    for url, page_id in external_urls.items():
        name = Path(urlsplit(url.split("?", 1)[0]).path).name
        if not name or not looks_like_file_url(name):
            continue
        local_name = local_filename_for_url(url)
        target = media_dir / local_name
        result = {"downloaded": True, "size": target.stat().st_size} if target.exists() else None
        if result is None or result["size"] > max_bytes:
            try:
                result = client.download(url, target, max_bytes=max_bytes)
            except HTTPError:
                continue
        item = {
            "url": url,
            "name": name,
            "sourcePageIds": [page_id],
            "mimetype": mimetypes.guess_type(name)[0],
        }
        if result["downloaded"]:
            item["localFileName"] = local_name
        else:
            item.update({
                "downloadSkipped": "size_limit",
                "downloadLimitBytes": max_bytes,
                "placeholder": True,
                "size": result.get("size"),
            })
        exported.append(item)
    return exported


def export_site(
    client,
    site_hostname,
    site_path,
    output,
    media_dir,
    folder_url=None,
    folder_title=None,
    max_bytes=MAX_DOWNLOAD_BYTES,
):
    site = client.get_json(f"/sites/{site_hostname}:{quote(site_path, safe='/')}")
    drive_items = []
    for drive in client.get_all(f"/sites/{site['id']}/drives"):
        drive_items.extend(collect_drive_items(client, drive["id"]))

    if folder_url:
        page = synthetic_folder_page(folder_url, folder_title or unquote(Path(urlsplit(folder_url).path).name))
        pages = [page]
        selected = discover_folder_files(page, drive_items, folder_url)
        external_urls = {}
    else:
        pages = client.get_all(f"/sites/{site['id']}/pages/microsoft.graph.sitePage?$expand=canvasLayout")
        selected, external_urls = discover_files(pages, drive_items)

    media_dir = Path(media_dir)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    exported_items = download_selected_files(client, selected, media_dir, max_bytes=max_bytes)
    external_downloads = download_external_urls(client, external_urls, media_dir, max_bytes=max_bytes)
    payload = {
        "site": site,
        "value": pages,
        "driveItems": exported_items,
        "referencedUrls": external_downloads,
        "extraction": {
            "siteHostname": site_hostname,
            "sitePath": site_path,
            "folderUrl": folder_url,
            "mediaDir": str(media_dir),
            "maxDownloadBytes": max_bytes,
            "downloadedDriveItems": sum("localFileName" in item for item in exported_items),
            "downloadedReferencedUrls": sum("localFileName" in item for item in external_downloads),
            "skippedOversize": sum(item.get("downloadSkipped") == "size_limit" for item in [*exported_items, *external_downloads]),
        },
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(pages)} pages and {len(exported_items)} drive files to {output}", flush=True)
    return payload


def parse_args():
    parser = argparse.ArgumentParser(description="Export SharePoint pages and referenced files.")
    parser.add_argument("--site-hostname", required=True, help="Example: tenenetsk.sharepoint.com")
    parser.add_argument("--site-path", required=True, help="Example: /sites/HRportal")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--folder-url", help="Export only files below this SharePoint folder URL")
    parser.add_argument("--folder-title", help="Knowledge page title for a folder-only export")
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=10,
        help="Skip local download above this size and keep source placeholders (default: 10)",
    )
    parser.add_argument("--token-command", help="Shell command that prints a Graph access token")
    return parser.parse_args()


def main():
    args = parse_args()
    export_site(
        GraphClient(token_from_args(args)),
        args.site_hostname,
        args.site_path,
        args.output,
        args.media_dir,
        folder_url=args.folder_url,
        folder_title=args.folder_title,
        max_bytes=args.max_file_size_mb * 1024 * 1024,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
