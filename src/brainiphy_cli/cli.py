"""brain — CLI for bootstrapping and maintaining graphify knowledge-graph
brains for a business, from zero data sources to a synced graph connected
to Claude Code and Claude Desktop.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path

import yaml

from brainiphy_cli import keychain, picker, sync as sync_mod, ui

TEMPLATE_DIR = Path(__file__).resolve().parent

REGISTRY_HEADER = """\
# Connector registry for this brain. Format consumed by `brain sync`.
#
# Every entry requires a script at connectors/<name>/sync.py — use
# `brain new-connector <project> <name>` to create one from the template.
"""

GITIGNORE_ENTRIES = [
    "connectors/state/",
    "connectors/logs/",
    "mirrors/",
    "raw/",
    "graphify-out/",
]

# connectors/ holds tooling (sync.py scripts, state, logs), not knowledge
# content — without this, graphify's AST extractor indexes the connector
# scripts themselves as source code (functions, imports) alongside the real
# data they produce in raw//mirrors/. Verified against detect.py: gitignore
# entries alone do NOT stop graphify from scanning a path, only
# .graphifyignore does (it has its own, gitignore-syntax-compatible parser).
GRAPHIFYIGNORE_ENTRIES = [
    "connectors/",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def _append_ignore_entries(path: Path, entries: list[str], header_comment: str) -> int:
    """Append any missing lines to a gitignore-syntax file, creating it if
    needed. Returns how many lines were added."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in entries if line not in existing]
    if not missing:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(f"\n{header_comment}\n")
        for line in missing:
            fh.write(line + "\n")
    return len(missing)


def _find_exe(name: str) -> str:
    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / name
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"{name} not found on PATH or in {Path(site.getuserbase()) / 'bin'}")


# ---------------------------------------------------------------- init ----

def cmd_init(args: argparse.Namespace) -> int:
    if args.project is None:
        # No path given: browse interactively when we have a terminal, and fall
        # back to the old default (cwd) when piped/scripted so automation that
        # ran bare `brain init` keeps working.
        if not picker.is_interactive():
            project = Path(".").resolve()
        else:
            chosen = picker.pick_project_dir()
            if chosen is None:
                ui.error("init cancelled")
                return 1
            project = chosen
    else:
        project = Path(args.project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    connectors_dir = project / "connectors"
    (connectors_dir / "state").mkdir(parents=True, exist_ok=True)

    ui.header("brain init", project)

    registry_path = connectors_dir / "registry.yaml"
    if registry_path.exists():
        ui.info("registry.yaml already exists, leaving it alone")
    else:
        registry_path.write_text(REGISTRY_HEADER + "\nconnectors: []\n", encoding="utf-8")
        ui.ok("created", registry_path.relative_to(project))

    added = _append_ignore_entries(
        project / ".gitignore", GITIGNORE_ENTRIES, "# brainiphy: generated output, do not version"
    )
    if added:
        ui.ok(f".gitignore: {added} new {'entry' if added == 1 else 'entries'}")
    else:
        ui.info(".gitignore already covers everything")

    added = _append_ignore_entries(
        project / ".graphifyignore",
        GRAPHIFYIGNORE_ENTRIES,
        "# brainiphy: do not index connector scripts as source code",
    )
    if added:
        ui.ok(f".graphifyignore: {added} new {'entry' if added == 1 else 'entries'}")
    else:
        ui.info(".graphifyignore already covers everything")

    try:
        ui.ok("graphify found:", _find_exe("graphify"))
    except FileNotFoundError:
        ui.warn("graphify is not installed")
        ui.hint("install it with:", "pip3 install --user graphifyy")

    ui.hint("next step:", f"brain new-connector {ui.short_path(project)} <name>")
    return 0


# --------------------------------------------------------- new-connector --

def _load_registry_entries(registry_path: Path) -> list[dict]:
    if not registry_path.exists():
        return []
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return data.get("connectors") or []


def _write_registry_entries(registry_path: Path, entries: list[dict]) -> None:
    body = yaml.safe_dump({"connectors": entries}, sort_keys=False, default_flow_style=False)
    registry_path.write_text(REGISTRY_HEADER + "\n" + body, encoding="utf-8")


def cmd_new_connector(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    name = args.name
    connector_dir = project / "connectors" / name
    script_path = connector_dir / "sync.py"

    if script_path.exists():
        ui.error("already exists, not overwriting it:", script_path)
        return 1

    ui.header("brain new-connector", f"{project}  ·  {name}")

    connector_dir.mkdir(parents=True, exist_ok=True)
    template = (TEMPLATE_DIR / "connector_template.py").read_text(encoding="utf-8")
    template = template.replace('SOURCE_SYSTEM = "REPLACE_ME"', f'SOURCE_SYSTEM = "{name}"')
    script_path.write_text(template, encoding="utf-8")
    script_path.chmod(0o755)
    ui.ok("created", script_path.relative_to(project))

    registry_path = project / "connectors" / "registry.yaml"
    entries = _load_registry_entries(registry_path)
    if any(e.get("name") == name for e in entries):
        ui.info(f"{name} was already in registry.yaml, not duplicating it")
    else:
        entries.append({"name": name, "interval_minutes": args.interval_minutes})
        _write_registry_entries(registry_path, entries)
        ui.ok(f"registered {name} in registry.yaml, every {args.interval_minutes:g} min")

    ui.hint("fill in fetch_records() in:", str(script_path))
    ui.hint("if it needs a credential:", f"brain secret set graphify-{_slug(project.name)}-{name}")
    return 0


# ------------------------------------------------------------------ sync --

def cmd_sync(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ui.header("brain sync" + (" (dry run)" if args.dry_run else ""), project)
    report = sync_mod.run(project, dry_run=args.dry_run)
    if args.dry_run:
        return 0

    summary = ui.table("", "", title=None)
    summary.add_row(ui.cell("ran", "brain.info"), ui.cell(", ".join(report.ran) or "—", "brain.ok"))
    summary.add_row(ui.cell("skipped", "brain.info"), ui.cell(", ".join(report.skipped) or "—"))
    summary.add_row(ui.cell("errors", "brain.info"), ui.cell(", ".join(report.errors) or "—", "brain.err" if report.errors else ""))
    summary.add_row(ui.cell("graph rebuilt", "brain.info"), ui.cell("yes" if report.graph_rebuilt else "no"))
    ui.blank()
    ui.print_table(summary)
    return 1 if report.errors else 0


# --------------------------------------------------------- connect-claude --

def cmd_connect_claude(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()

    ui.header("brain connect-claude", project)

    with ui.working("graphify claude install"):
        result = subprocess.run(["graphify", "claude", "install"], cwd=project, capture_output=True, text=True)
    ui.raw(result.stdout)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("graphify claude install failed")
        return 1
    ui.ok("Claude Code wired up (CLAUDE.md + hooks)")

    if not (args.desktop or args.trust_desktop):
        return 0

    config_path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if not config_path.exists():
        ui.error("no Claude Desktop config — is it installed?", config_path)
        return 1

    backup_path = config_path.with_name(config_path.name + f".bak-{_timestamp()}")
    shutil.copy(config_path, backup_path)
    ui.ok("backup saved to", backup_path)

    data = json.loads(config_path.read_text(encoding="utf-8"))

    if args.desktop:
        graphify_mcp = _find_exe("graphify-mcp")
        server_name = f"graphify-{_slug(project.name)}"
        data.setdefault("mcpServers", {})
        data["mcpServers"][server_name] = {
            "command": graphify_mcp,
            "args": ["--graph", str(project / "graphify-out" / "graph.json")],
        }
        ui.ok(f"MCP server '{server_name}' registered ->", project / "graphify-out/graph.json")

    if args.trust_desktop:
        prefs = data.setdefault("preferences", {})
        trusted = prefs.setdefault("localAgentModeTrustedFolders", [])
        if str(project) not in trusted:
            trusted.append(str(project))
            ui.ok("added to localAgentModeTrustedFolders (existing entries preserved):", project)
        else:
            ui.info("already in localAgentModeTrustedFolders")

    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    ui.hint("restart Claude Desktop to apply the changes")
    return 0


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d%H%M%S")


# -------------------------------------------------------------- schedule --

def cmd_schedule(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    registry_path = project / "connectors" / "registry.yaml"
    entries = _load_registry_entries(registry_path)
    if not entries:
        ui.error("no connectors registered — nothing worth scheduling yet")
        return 1

    ui.header("brain schedule", project)

    slug = args.slug or _slug(project.name)
    brain_exe = _find_exe("brain")
    log_dir = project / "connectors" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_text = (TEMPLATE_DIR / "launchd_template.plist").read_text(encoding="utf-8")
    plist_text = (
        plist_text.replace("__PROJECT_SLUG__", slug)
        .replace("__BRAIN_EXE__", brain_exe)
        .replace("__PROJECT_PATH__", str(project))
        .replace("__INTERVAL_SECONDS__", str(int(args.interval_minutes * 60)))
        .replace("__LOG_DIR__", str(log_dir))
    )

    plist_path = Path.home() / "Library/LaunchAgents" / f"com.graphify.sync.{slug}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_text, encoding="utf-8")
    ui.ok("wrote", plist_path)

    load_cmd = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    if args.load:
        result = subprocess.run(load_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            ui.raw(result.stderr, stderr=True)
            ui.error("launchctl bootstrap failed")
            return 1
        ui.ok(f"loaded with launchctl, runs every {args.interval_minutes:g} min")
    else:
        ui.hint("not loaded yet — to activate it:", " ".join(load_cmd))
    return 0


# ---------------------------------------------------------------- secret --

def cmd_secret_set(args: argparse.Namespace) -> int:
    value = getpass.getpass(f"Value for {args.item} (input hidden): ")
    if not value:
        ui.error("empty value, cancelled")
        return 1
    keychain.set_secret(args.item, value)
    ui.ok("stored in the Keychain:", args.item)
    return 0


def cmd_secret_get(args: argparse.Namespace) -> int:
    try:
        # Plain print, not ui: the value is meant to be piped/captured, so it
        # must stay unstyled and alone on stdout.
        print(keychain.get_secret(args.item))
    except keychain.SecretNotFoundError as exc:
        ui.error(str(exc))
        return 1
    return 0


# ---------------------------------------------------------------- status --

def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    entries = _load_registry_entries(project / "connectors" / "registry.yaml")
    ui.header("brain status", project)

    if not entries:
        ui.info("no connectors registered yet")
        ui.hint("add one with:", f"brain new-connector {ui.short_path(project)} <name>")
    else:
        table = ui.table("connector", "interval", "state", "script")
        for entry in entries:
            name = entry["name"]
            interval = float(entry.get("interval_minutes", 60))
            due = sync_mod.is_due(project, name, interval)
            script_ok = (project / "connectors" / name / "sync.py").exists()
            table.add_row(
                ui.cell(name, "brain.path"),
                ui.cell(f"{interval:g} min"),
                ui.cell("due now", "brain.warn") if due else ui.cell("up to date", "brain.ok"),
                ui.cell("ok", "brain.ok") if script_ok else ui.cell("MISSING", "brain.err"),
            )
        ui.print_table(table)

    ui.blank()
    graph_json = project / "graphify-out" / "graph.json"
    if graph_json.exists():
        try:
            data = json.loads(graph_json.read_text(encoding="utf-8"))
            nodes = len(data.get("nodes", []))
            edges = len(data.get("links", data.get("edges", [])))
            ui.ok(f"graph: {nodes} nodes, {edges} edges", graph_json)
        except json.JSONDecodeError:
            ui.warn("graph exists but could not be parsed:", graph_json)
    else:
        ui.warn("graph not built yet")
        ui.hint("build it with:", f"graphify update {ui.short_path(project)}")
    return 0


# ------------------------------------------------------------------ main --

def main() -> int:
    parser = argparse.ArgumentParser(prog="brain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Set up connectors/ and .gitignore in a project")
    p.add_argument("project", nargs="?", default=None, help="Target folder; omit it to pick one interactively")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-connector", help="Create a new connector from the template")
    p.add_argument("project")
    p.add_argument("name")
    p.add_argument("--interval-minutes", type=float, default=60)
    p.set_defaults(func=cmd_new_connector)

    p = sub.add_parser("sync", help="Run due connectors and rebuild the graph")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("connect-claude", help="Connect the project to Claude Code / Desktop")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--desktop", action="store_true", help="Register an MCP server in Claude Desktop")
    p.add_argument("--trust-desktop", action="store_true", help="Add the project to localAgentModeTrustedFolders (additive)")
    p.set_defaults(func=cmd_connect_claude)

    p = sub.add_parser("schedule", help="Generate and install a LaunchAgent for periodic `brain sync`")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--interval-minutes", type=float, default=15)
    p.add_argument("--slug", default=None)
    p.add_argument("--load", action="store_true", help="Load it immediately with launchctl")
    p.set_defaults(func=cmd_schedule)

    secret = sub.add_parser("secret", help="Manage credentials in the Keychain")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True)
    sp = secret_sub.add_parser("set")
    sp.add_argument("item")
    sp.set_defaults(func=cmd_secret_set)
    sp = secret_sub.add_parser("get")
    sp.add_argument("item")
    sp.set_defaults(func=cmd_secret_get)

    p = sub.add_parser("status", help="Show connector and graph status")
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
