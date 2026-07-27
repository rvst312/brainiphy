#!/usr/bin/env python3
"""Template for a connector that pulls from a REST API.

`brain new-connector <project> <name> --api <base-url>` copies this file to
<project>/connectors/<name>/sync.py with SOURCE_SYSTEM, BASE_URL and
SECRET_ITEM pre-filled.

The network plumbing is not here on purpose — HttpClient already does the
retrying, the pagination and the "this token has no scope for that" handling
(see brainiphy_cli/httpclient.py for why each of those is not optional), and
collect.run() does the per-object loop, the --probe mode and the exit code.
What is left for you is the part only you can know: which endpoints exist, and
what a record from each one should look like as a document.

To implement it:
  1. Write one collect_* function per kind of thing you want in the brain.
     Return a list of dicts with id/title/body; any other key becomes a
     frontmatter field.
  2. List them in COLLECTORS with the subfolder their records belong in.
  3. Run `sync.py --out /tmp/probe --probe` to see which ones the credential
     can actually read. Expect some to come back "no scope" — that is the
     normal shape of an API token, not a bug.

Contract with `brain sync` (unchanged): accept --out <dir>, exit 0 on success,
print a human-readable summary. Credentials come from the Keychain only —
never a CLI argument, never hardcoded.
"""
from __future__ import annotations

import sys

from brainiphy_cli import collect
from brainiphy_cli.collect import Collector, kv_block
from brainiphy_cli.httpclient import HttpClient

SOURCE_SYSTEM = "REPLACE_ME"

# Root of the API, without a trailing slash.
BASE_URL = "REPLACE_ME_BASE_URL"

# Keychain item holding the credential. Register it with `brain secret set`.
SECRET_ITEM = "REPLACE_ME_SECRET"

# Anything the API needs on every request beyond auth — an API version pin, a
# tenant header. Many APIs reject requests without one and the error rarely
# says so.
EXTRA_HEADERS: dict[str, str] = {}

client = HttpClient(BASE_URL, secret_item=SECRET_ITEM, extra_headers=EXTRA_HEADERS)


# --- collectors -------------------------------------------------------------
# Each returns a list of records. A record needs id/title/body; extra keys land
# in the frontmatter, where they can be filtered without parsing prose.
#
# Two things worth doing here, both learned the hard way:
#   - Resolve foreign keys to names. `stage_id: f7a80aa4-…` is dead weight in a
#     graph; `stage: "Awaiting payment"` is the thing people ask about. If a
#     lookup lives in another collector, stash it in a module-level dict and
#     order COLLECTORS so the lookup is filled first.
#   - Use a stable remote id for the record id. write_record() slugs it into the
#     filename, so re-runs overwrite in place instead of piling up duplicate
#     nodes every sync.

def collect_things() -> list[dict]:
    raise NotImplementedError("write one collect_* function per object you want in the brain")

    # Sketch of the real shape, once you delete the line above:
    rows = client.paginate("/v1/things", {"limit": 100}, "things")
    return [
        {
            "id": f"thing-{row['id']}",
            "title": f"Thing: {row.get('name')}",
            "body": kv_block(row, ["description", "status", "owner", "createdAt"]),
            "record_type": "thing",
        }
        for row in rows
    ]


COLLECTORS = [
    Collector("things", subfolder="things", fetch=collect_things),
]


def main() -> int:
    return collect.run(SOURCE_SYSTEM, COLLECTORS)


if __name__ == "__main__":
    sys.exit(main())
