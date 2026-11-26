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

import sys

from brainiphy_cli.collect import Collector, run
from brainiphy_cli.keychain import get_secret  # noqa: F401  (import here for connectors that need it)


SOURCE_SYSTEM = "REPLACE_ME"  # e.g. "hubspot-crm", "local-desktop-clientes2"
SECRET_ITEM = "REPLACE_ME_SECRET"


def fetch_records() -> list[dict]:
    """Replace this with real fetch logic for the source.

    Must return a list of dicts, each with at minimum:
        {"id": <stable remote id>, "title": <str>, "body": <str>}
    Any other keys are written into the frontmatter as extra fields.

    Return the complete snapshot, including an empty list if all records were
    deleted. Raise on any incomplete fetch so the previous snapshot survives.
    For a REST API, prefer the --api template and its HttpClient instead.
    Read credentials with get_secret(SECRET_ITEM).
    """
    raise NotImplementedError("fetch_records() not implemented — fill this in for the real source")


def main() -> int:
    return run(SOURCE_SYSTEM, [Collector("records", ".", fetch_records, snapshot=True)])


if __name__ == "__main__":
    sys.exit(main())
