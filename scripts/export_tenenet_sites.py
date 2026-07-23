#!/usr/bin/env python3
"""Export the remaining TENENET SharePoint migration sites."""

import argparse
from pathlib import Path

from export_graph_site import GraphClient, export_site, token_from_args


SITE_HOSTNAME = "tenenetsk.sharepoint.com"
SITES = (
    {
        "key": "ambulancia-kli-psy",
        "site_path": "/sites/AmbulanciaKLIPSY",
    },
    {
        "key": "apzn2",
        "site_path": "/sites/APZN2",
    },
    {
        "key": "scpp-dokumenty",
        "site_path": "/sites/managment",
        "folder_title": "SCPP dokumenty",
        "folder_url": (
            "https://tenenetsk.sharepoint.com/sites/managment/"
            "Zdielane%20dokumenty/SCPP%20Dokumenty"
        ),
    },
    {
        "key": "hr-finance-tim",
        "site_path": "/sites/HRtim",
    },
    {
        "key": "assp",
        "site_path": "/sites/ASSP",
    },
    {
        "key": "komteam",
        "site_path": "/sites/Web",
        # KomTeam stores its content in the Documents library rather than on
        # the single site page, so retain the complete library in its snapshot.
        "include_all_drive_files": True,
    },
    {
        "key": "svi",
        "site_path": "/sites/VCItm",
        "folder_title": "SVI",
        "folder_url": (
            "https://tenenetsk.sharepoint.com/sites/VCItm/Zdielane%20dokumenty/"
            "METODIKA%20SVI/SVI%20-%20new/Dokumenty%20SVI"
        ),
    },
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Directory for ignored JSON snapshots and media directories",
    )
    parser.add_argument("--only", choices=[site["key"] for site in SITES], action="append")
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=10,
        help="Skip downloads above this size and preserve source placeholders",
    )
    parser.add_argument("--token-command", help="Shell command that prints a Graph access token")
    return parser.parse_args()


def main():
    args = parse_args()
    client = GraphClient(token_from_args(args))
    selected = set(args.only or ())
    for site in SITES:
        if selected and site["key"] not in selected:
            continue
        key = site["key"]
        export_site(
            client,
            SITE_HOSTNAME,
            site["site_path"],
            args.output_dir / f"{key}-pages.json",
            args.output_dir / f"{key}-media",
            folder_url=site.get("folder_url"),
            folder_title=site.get("folder_title"),
            include_all_drive_files=site.get("include_all_drive_files", False),
            max_bytes=args.max_file_size_mb * 1024 * 1024,
        )


if __name__ == "__main__":
    main()
