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
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from brainiphy_cli.frontmatter import write_record, read_metadata, record_filename, slugify
from brainiphy_cli.httpclient import NoScope
from brainiphy_cli.keychain import SecretNotFoundError
from brainiphy_cli.storage import atomic_write, contained_path, exclusive_lock, validate_name

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
    # Older generated scripts did not promise completeness on malformed API
    # responses. Enable deletion reconciliation only after auditing the fetch.
    snapshot: bool = False


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


def _publish(out: Path, collector: Collector, records: list[Record],
             source_system: str, *, peers: list[Collector]) -> None:
    """Stage a validated batch; snapshot collectors reconcile their owned files.

    Manifests keep collectors sharing a folder (e.g. tags and custom fields)
    from pruning one another. Legacy files can be adopted by an exclusive
    collector; shared folders only adopt records present in the new snapshot.
    """
    validate_name(collector.name)
    if Path(collector.subfolder).is_absolute() or ".." in Path(collector.subfolder).parts:
        raise ValueError("collector subfolder must stay under --out")
    target = contained_path(out, collector.subfolder)
    manifest = contained_path(out, ".brainiphy-state", f"{collector.name}.json")
    folder = str(target.relative_to(out))
    previous: set[str] = set()
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("subfolder") != folder
                or not isinstance(data.get("files"), list)
                or not all(isinstance(p, str) for p in data["files"])):
            raise ValueError("invalid collector manifest; previous files preserved")
        for name in data["files"]:
            if Path(name).name != name or not name.endswith(".md"):
                raise ValueError("invalid filename in collector manifest")
            contained_path(target, name)
        previous.update(data["files"])
    elif len(peers) == 1 and target.exists():
        previous.update(p.name for p in target.glob("*.md")
                        if read_metadata(p).get("source_system") == source_system)

    occupied: set[str] = set()
    for peer in peers:
        if peer.name == collector.name:
            continue
        peer_manifest = contained_path(out, ".brainiphy-state", f"{validate_name(peer.name)}.json")
        if peer_manifest.exists():
            data = json.loads(peer_manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("subfolder") != folder:
                raise ValueError("collector folder changed; migrate its manifest before publishing")
            occupied.update(data["files"])
    previous.difference_update(occupied)

    current: set[str] = set()
    migrated: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="brainiphy-collector-") as temporary:
        stage = Path(temporary)
        for record in records:
            name = record_filename(record["id"])
            if name in occupied:
                raise ValueError(f"record id is already owned by another collector: {record['id']!r}")
            if name in current:
                raise ValueError(f"duplicate record id: {record['id']!r}")
            current.add(name)
            legacy_name = f"{slugify(record['id'])}.md"
            for candidate in (name, legacy_name):
                existing = contained_path(target, candidate)
                metadata = read_metadata(existing)
                if (str(metadata.get("source_id")) == str(record["id"])
                        and metadata.get("source_system") == source_system):
                    shutil.copyfile(existing, stage / candidate)
                    previous.add(candidate)
                    if candidate != name:
                        migrated.add(candidate)
            write_record(
                stage, record_id=record["id"], title=record["title"], body=record["body"],
                source_system=source_system,
                extra_fields={k: v for k, v in record.items() if k not in ("id", "title", "body")},
            )

        # No published file changes until every record has been validated.
        for name in current:
            destination = contained_path(target, name)
            content = (stage / name).read_text(encoding="utf-8")
            if not destination.exists() or destination.read_text(encoding="utf-8") != content:
                atomic_write(destination, content)
        # Retain cleanup work across an interrupted publish.
        atomic_write(manifest, json.dumps({"subfolder": folder, "files": sorted(previous | current)}))
        removable = (previous - current if collector.snapshot else set()) | migrated
        for name in removable:
            stale = contained_path(target, name)
            if read_metadata(stale).get("source_system") == source_system:
                stale.unlink()
        retained = current if collector.snapshot else (previous | current) - migrated
        atomic_write(manifest, json.dumps({"subfolder": folder, "files": sorted(retained)}))


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

    if len({c.name for c in collectors}) != len(collectors):
        print(f"[{source_system}] collector names must be unique", file=sys.stderr)
        return 2

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
            if not isinstance(records, list):
                raise ValueError("collector must return a complete list of records")
            if not args.probe:
                target = contained_path(args.out, collector.subfolder).resolve()
                peers = [c for c in collectors if contained_path(args.out, c.subfolder).resolve() == target]
                with exclusive_lock(contained_path(args.out, ".brainiphy-state", ".publish.lock")):
                    _publish(args.out, collector, records, source_system, peers=peers)
                written += len(records)
        except SecretNotFoundError as exc:
            # Not a per-object failure: no collector can run without the
            # credential, so reporting it once per object buries the single
            # line that says how to fix it under nine identical copies — and
            # _short() truncates the instruction away in each of them.
            print(f"[{source_system}] {exc}", file=sys.stderr)
            return 1
        except NoScope as exc:
            results.append(Result(collector.name, f"no scope ({_short(exc)})"))
            continue
        except Exception as exc:  # noqa: BLE001 — one bad object must not sink the rest
            results.append(
                Result(collector.name, f"ERROR {type(exc).__name__}: {_short(exc, 90)}", failed=True)
            )
            continue

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
