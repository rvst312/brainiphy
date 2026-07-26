#!/usr/bin/env python3
"""Template for a brainiphy connector.

`brain new-connector <project> <name>` copies this file to
<project>/connectors/<name>/sync.py and pre-fills SOURCE_SYSTEM. Adapt
fetch_records() to the real source — nothing else here should normally
need to change.

Contract (what `brain sync` expects from any connector script):
  - Accepts `--out <dir>` and writes its output there as normalized
    Markdown files (see brainiphy_cli.frontmatter.write_record).
  - File names are stable per remote record ID (write_record handles this)
    so re-runs overwrite in place instead of duplicating.
  - Exits 0 on success, non-zero on failure, with a human-readable summary
    printed to stdout.
  - Reads credentials only via brainiphy_cli.keychain.get_secret(<item>) —
    set them once with `brain secret set <item>`. Never hardcode a token,
    never accept one as a CLI argument (shell history / process listings /
    launchd logs would all leak it).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from brainiphy_cli.frontmatter import write_record
from brainiphy_cli.keychain import get_secret  # noqa: F401  (import here for connectors that need it)


SOURCE_SYSTEM = "REPLACE_ME"  # e.g. "hubspot-crm", "local-desktop-clientes2"


def fetch_records() -> list[dict]:
    """Replace this with real fetch logic for the source.

    Must return a list of dicts, each with at minimum:
        {"id": <stable remote id>, "title": <str>, "body": <str>}
    Any other keys are written into the frontmatter as extra fields.

    Example using a REST API with a Keychain-stored token:
        import urllib.request, json
        token = get_secret("graphify-<project-slug>-<source-name>")
        req = urllib.request.Request(
            "https://api.example.com/v3/records",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        return [{"id": r["id"], "title": r["name"], "body": r["notes"]} for r in data["results"]]
    """
    raise NotImplementedError("fetch_records() not implemented — fill this in for the real source")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    records = fetch_records()
    for record in records:
        write_record(
            args.out,
            record_id=record["id"],
            title=record["title"],
            body=record["body"],
            source_system=SOURCE_SYSTEM,
            extra_fields={k: v for k, v in record.items() if k not in ("id", "title", "body")},
        )

    print(f"[{SOURCE_SYSTEM}] wrote {len(records)} record(s) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
