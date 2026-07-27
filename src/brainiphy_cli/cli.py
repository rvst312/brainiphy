"""brain — CLI for bootstrapping and maintaining graphify knowledge-graph
brains for a business, from zero data sources to a synced graph connected
to Claude Code and Claude Desktop.

Start with `brain new` (guided, asks questions) or `brain guide` (shows what a
brain needs and how far along this one is). Everything either of them does is
also available as an individual command below.

This module is argparse plumbing only: the real work lives in project.py
(scaffolding, connectors, Claude, scheduling), sync.py (running connectors),
steps.py (what the playbook is and how far along a project is) and wizard.py
(the guided flow).
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from brainiphy_cli import keychain, picker, project as project_mod, steps, sync as sync_mod, ui, wizard


# ----------------------------------------------------------------- new ----

def cmd_new(args: argparse.Namespace) -> int:
    return wizard.run(args.project)


# --------------------------------------------------------------- guide ----

def cmd_guide(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ui.header("Building a brain", project)
    steps.render(steps.inspect(project), verbose=args.verbose)
    return 0


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

    ui.header("brain init", project)
    project_mod.scaffold(project)

    try:
        ui.ok("graphify found:", project_mod.find_exe("graphify"))
    except FileNotFoundError:
        ui.warn("graphify is not installed")
        ui.hint("install it with:", "pip3 install --user graphifyy")

    ui.hint("next step — add a data source:", f"brain new {ui.short_path(project)}")
    ui.hint("or see the whole process:", f"brain guide {ui.short_path(project)}")
    return 0


# --------------------------------------------------------- new-connector --

def cmd_new_connector(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    name = args.name

    mirror = None
    if args.mirror:
        mirror = Path(args.mirror).expanduser().resolve()
        if not mirror.is_dir():
            ui.error("--mirror needs an existing folder:", mirror)
            return 1

    ui.header("brain new-connector", f"{project}  ·  {name}")

    script_path = project_mod.create_connector(
        project, name, interval_minutes=args.interval_minutes, mirror=mirror
    )
    if script_path is None:
        return 1

    if mirror:
        ui.ok("ready to run — it mirrors", mirror)
        ui.hint("pull it in now with:", f"brain sync {ui.short_path(project)}")
    else:
        ui.hint("fill in fetch_records() in:", str(script_path))
        ui.hint(
            "if it needs a credential:",
            f"brain secret set {project_mod.secret_item_name(project, name)}",
        )
    return 0


# ------------------------------------------------------------------ sync --

def cmd_sync(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ui.header("brain sync" + (" (dry run)" if args.dry_run else ""), project)
    report = sync_mod.run(project, dry_run=args.dry_run, full=args.full)
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
    ok = project_mod.connect_claude(project, desktop=args.desktop, trust_desktop=args.trust_desktop)
    return 0 if ok else 1


# -------------------------------------------------------------- schedule --

def cmd_schedule(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    ui.header("brain schedule", project)
    ok = project_mod.schedule(
        project,
        interval_minutes=args.interval_minutes,
        slug_override=args.slug,
        load=args.load,
    )
    return 0 if ok else 1


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
    entries = project_mod.load_registry_entries(project)
    ui.header("brain status", project)

    if not entries:
        ui.info("no connectors registered yet")
        ui.hint("add one interactively with:", f"brain new {ui.short_path(project)}")
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
        ui.hint("build it with:", f"brain sync {ui.short_path(project)}")

    # Where this project sits in the overall process — status answers "what is
    # the state of my connectors", this answers "what do I do next".
    state = steps.inspect(project)
    ui.blank()
    if state.complete:
        ui.ok(f"setup complete ({state.done_count}/{len(state.steps)} steps)")
    else:
        next_step = state.next_step
        ui.info(f"setup: {state.done_count}/{len(state.steps)} steps done — next is '{next_step.title}'")
        ui.hint("see the whole process with:", f"brain guide {ui.short_path(project)}")
    return 0


# ------------------------------------------------------------------ main --

def main() -> int:
    parser = argparse.ArgumentParser(prog="brain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="Guided end-to-end setup: build a brain step by step")
    p.add_argument("project", nargs="?", default=None, help="Target folder; omit it to pick one interactively")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("guide", help="Show the 7 steps of building a brain and how far along this one is")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--verbose", action="store_true", help="Also show details for the steps already done")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("init", help="Set up connectors/ and .gitignore in a project")
    p.add_argument("project", nargs="?", default=None, help="Target folder; omit it to pick one interactively")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-connector", help="Create a new connector from the template")
    p.add_argument("project")
    p.add_argument("name")
    p.add_argument("--interval-minutes", type=float, default=60)
    p.add_argument(
        "--mirror",
        metavar="FOLDER",
        help="Generate a ready-to-run connector that mirrors a local folder (no fetch_records to write)",
    )
    p.set_defaults(func=cmd_new_connector)

    p = sub.add_parser("sync", help="Run due connectors and rebuild the graph")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--full",
        action="store_true",
        help="Full re-index (graphify extract) instead of the incremental code-only update. "
        "Needed to pick up document changes; happens automatically on the first build.",
    )
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
