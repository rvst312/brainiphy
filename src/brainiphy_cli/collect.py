"""The run loop every multi-object connector needs.

A connector against a real system is rarely one list of records: a CRM has
contacts *and* deals *and* pipelines, and a token is typically allowed to read
some of them and not others. Rather than have each connector reinvent the
"try each object, skip the ones we cannot read, report what happened" loop,
a connector declares its Collectors and calls run().

What this buys a connector for free:
  - `--probe`: report what the credential can actually read, writing nothing.
    Scope discovery has to be empirical — no API tells you which scopes a token
    was issued with — so the first thing you want from a new connector is this.
  - Per-object isolation: one object 401ing (no scope) or blowing up does not
    cost you the other nine.
  - An exit code that means something: missing scopes are a normal outcome and
    exit 0; a real API or parse failure exits 1 so `brain sync` reports it.

Collectors run in list order, and may depend on that: a collector can stash
lookups (e.g. pipeline id -> name) that a later one reads. Order the list so
the dependency comes first, and make the dependent one degrade gracefully when
the earlier one had no scope.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from brainiphy_cli.frontmatter import write_record
from brainiphy_cli.httpclient import NoScope

# A record is a plain dict: id/title/body are required, every other key becomes
# a frontmatter field. Keep the extras flat and filterable — the point of
# putting something in frontmatter rather than prose is that it can be grepped
# and queried without parsing English.
Record = dict


@dataclass
class Collector:
    """One kind of thing to pull, and where its records go under --out."""

    name: str                      # label in the summary table
    subfolder: str                 # folder under --out for its records
    fetch: Callable[[], list[Record]]
    note: str = ""                 # optional caveat shown next to the result


@dataclass
class Result:
    name: str
    status: str
    count: int = 0
    failed: bool = False


def kv_block(data: dict, keys: list[str]) -> str:
    """Render selected fields as a Markdown list, skipping empty ones.

    Used for record bodies: the graph is built by a model reading these files,
    so a readable labelled list beats a JSON dump.
    """
    lines = []
    for key in keys:
        value = data.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines)


def build_parser(description: str = "") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--out", required=True, type=Path,
                        help="Directory to write normalized Markdown into")
    parser.add_argument("--probe", action="store_true",
                        help="Report what the credential can read; write nothing")
    parser.add_argument("--only", metavar="NAME", action="append",
                        help="Only run these collectors (repeatable)")
    return parser


def run(
    source_system: str,
    collectors: list[Collector],
    *,
    context: str = "",
    argv: list[str] | None = None,
) -> int:
    """Run every collector, write what came back, print a summary. Returns an
    exit code suitable for returning straight out of main().

    `context` is a short line identifying which account/tenant is being read —
    worth printing, since pointing a connector at the wrong tenant otherwise
    looks exactly like pointing it at an empty one.
    """
    args = build_parser(f"{source_system} connector").parse_args(argv)

    selected = collectors
    if args.only:
        wanted = set(args.only)
        selected = [c for c in collectors if c.name in wanted]
        unknown = wanted - {c.name for c in collectors}
        if unknown:
            print(f"[{source_system}] unknown collector(s): {', '.join(sorted(unknown))}",
                  file=sys.stderr)
            return 2

    results: list[Result] = []
    written = 0

    for collector in selected:
        try:
            records = collector.fetch()
        except NoScope as exc:
            results.append(Result(collector.name, f"no scope ({_short(exc)})"))
            continue
        except Exception as exc:  # noqa: BLE001 — one bad object must not sink the rest
            results.append(
                Result(collector.name, f"ERROR {type(exc).__name__}: {_short(exc, 90)}", failed=True)
            )
            continue

        if not args.probe:
            for record in records:
                write_record(
                    args.out / collector.subfolder,
                    record_id=record["id"],
                    title=record["title"],
                    body=record["body"],
                    source_system=source_system,
                    extra_fields={k: v for k, v in record.items()
                                  if k not in ("id", "title", "body")},
                )
            written += len(records)

        status = f"ok, {len(records)} record(s)"
        if collector.note:
            status += f" — {collector.note}"
        results.append(Result(collector.name, status, count=len(records)))

    _print_summary(source_system, context, results, args.probe, written, args.out)
    return 1 if any(r.failed for r in results) else 0


def _short(exc: Exception, limit: int = 70) -> str:
    return str(exc).replace("\n", " ")[:limit]


def _print_summary(
    source_system: str,
    context: str,
    results: list[Result],
    probe: bool,
    written: int,
    out: Path,
) -> None:
    head = f"[{source_system}]"
    if context:
        head += f" {context}"
    if probe:
        head += " (probe, nothing written)"
    print(head)

    if results:
        width = max(len(r.name) for r in results)
        for result in results:
            print(f"  {result.name.ljust(width)}  {result.status}")

    if not probe:
        print(f"[{source_system}] wrote {written} record(s) to {out}")
