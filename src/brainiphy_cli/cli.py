"""brain — CLI for bootstrapping and maintaining graphify knowledge-graph
brains for a business, from zero data sources to a synced graph connected
to Claude Code and Claude Desktop.

Bare `brain` (or `brain new`) opens the app: the seven steps of building a
brain, walked in order, inside the CLI. Everything it does is also available as
an individual command below — which is what a script or an agent should use,
since the app needs a terminal.

This module is argparse plumbing only: the real work lives in project.py
(scaffolding, connectors, Claude, scheduling), sync.py (running connectors),
steps.py (what the playbook is and how far along a project is), app.py (the
flow) and actions.py (the interactive operations a step performs).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from brainiphy_cli import (
    app,
    keychain,
    keys,
    picker,
    presets,
    project as project_mod,
    steps,
    sync as sync_mod,
    ui,
)


# ----------------------------------------------------------------- new ----

def cmd_new(args: argparse.Namespace) -> int:
    """`brain new` and bare `brain` are the same thing: the app.

    They used to be a one-shot wizard and a menu, which meant three front doors
    (with `brain init`) and no single answer to "how do I drive this". The app
    is the flow now; this name is kept because it is the one the docs and the
    CLI's own hints point at.
    """
    return app.run(args.project)


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

    # Scaffolding is step 2 of a seven-step process, so at a terminal, carry
    # straight on into it rather than printing the next command and stopping.
    if picker.is_interactive() and keys.supported():
        ui.blank()
        ui.hint("continuing into the setup — press any key", "")
        try:
            keys.read_key()
        except (KeyboardInterrupt, EOFError):
            return 0
        return app.run(str(project))

    ui.hint("next step — add a data source:", f"brain new {ui.short_path(project)}")
    ui.hint("or see the whole process:", f"brain guide {ui.short_path(project)}")
    return 0


# --------------------------------------------------------- new-connector --

def _parse_vars(pairs: list[str] | None) -> dict[str, str] | None:
    """--var LOCATION_ID=abc123 … into a dict. None signals a malformed pair."""
    variables: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            ui.error("--var needs NAME=VALUE, got:", pair)
            return None
        key, value = pair.split("=", 1)
        variables[key.strip()] = value
    return variables


def cmd_new_connector(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    name = args.name

    exclusive = [bool(args.mirror), bool(args.preset), bool(args.api)]
    if sum(exclusive) > 1:
        ui.error("--mirror, --preset and --api pick different templates — use one")
        return 1

    mirror = None
    if args.mirror:
        mirror = Path(args.mirror).expanduser().resolve()
        if not mirror.is_dir():
            ui.error("--mirror needs an existing folder:", mirror)
            return 1

    variables = _parse_vars(args.var)
    if variables is None:
        return 1

    ui.header("brain new-connector", f"{project}  ·  {name}")

    script_path = project_mod.create_connector(
        project,
        name,
        interval_minutes=args.interval_minutes,
        mirror=mirror,
        preset=args.preset,
        api_base=args.api,
        variables=variables,
    )
    if script_path is None:
        return 1

    secret_item = project_mod.secret_item_name(project, name)
    short = ui.short_path(project)

    if mirror:
        ui.ok("ready to run — it mirrors", mirror)
        ui.hint("pull it in now with:", f"brain sync {short}")
        return 0

    if args.preset:
        preset = presets.get(args.preset)
        for note in preset.notes:
            ui.info(note)
        ui.hint("store the credential:", f"brain secret set {secret_item}")
        # --probe before the first real sync: it reports which objects the
        # credential can actually read, without writing anything.
        ui.hint("then see what it can read:", f"{script_path} --out /tmp/probe --probe")
        ui.hint("then pull it in:", f"brain sync {short}")
        return 0

    if args.api:
        ui.hint("write one collect_* function per object, then list it in COLLECTORS:",
                str(script_path))
        ui.hint("store the credential:", f"brain secret set {secret_item}")
        ui.hint("check what it can read:", f"{script_path} --out /tmp/probe --probe")
        return 0

    ui.hint("fill in fetch_records() in:", str(script_path))
    ui.hint("if it needs a credential:", f"brain secret set {secret_item}")
    return 0


# -------------------------------------------------------------- presets ----

def cmd_presets(args: argparse.Namespace) -> int:
    ui.header("brain presets", "connectors that are already written")
    table = ui.table("preset", "system", "pulls")
    for name in presets.names():
        preset = presets.PRESETS[name]
        table.add_row(
            ui.cell(name, "brain.path"),
            ui.cell(preset.title),
            ui.cell(preset.description),
        )
    ui.print_table(table)

    ui.blank()
    for name in presets.names():
        preset = presets.PRESETS[name]
        if not preset.variables:
            continue
        ui.info(f"{name} needs:")
        for var in preset.variables:
            ui.hint(f"  {var.name} — {var.prompt}", var.where or var.example)

    ui.blank()
    ui.hint("install one with:",
            f"brain new-connector <project> <name> --preset {presets.names()[0]} --var NAME=VALUE")
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
    ui.header("brain status", project)
    steps.render_status(project)
    return 0


# ------------------------------------------------------------------ main --

def main() -> int:
    parser = argparse.ArgumentParser(prog="brain", description=__doc__)
    # Not required: bare `brain` opens the app. Every command below is still
    # available by name, which is what a script or an agent should use.
    sub = parser.add_subparsers(dest="command", required=False)

    p = sub.add_parser("new", help="Open the app: walk the seven steps (same as bare `brain`)")
    p.add_argument("project", nargs="?", default=None, help="Target folder; omit it to pick one interactively")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("guide", help="Show the 7 steps of building a brain and how far along this one is")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--verbose", action="store_true", help="Also show details for the steps already done")
    p.set_defaults(func=cmd_guide, framed=True)

    p = sub.add_parser("init", help="Set up connectors/ and .gitignore in a project")
    p.add_argument("project", nargs="?", default=None, help="Target folder; omit it to pick one interactively")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-connector", help="Create a new connector from a preset or a template")
    p.add_argument("project")
    p.add_argument("name")
    p.add_argument("--interval-minutes", type=float, default=60)
    p.add_argument(
        "--mirror",
        metavar="FOLDER",
        help="Ready-to-run connector that mirrors a local folder (nothing to fill in)",
    )
    p.add_argument(
        "--preset",
        metavar="NAME",
        choices=presets.names(),
        help="Install a finished connector for a known system (see `brain presets`)",
    )
    p.add_argument(
        "--api",
        metavar="BASE_URL",
        help="Connector for a REST API: retries, pagination and scope handling done, "
        "endpoints left for you to write",
    )
    p.add_argument(
        "--var",
        metavar="NAME=VALUE",
        action="append",
        help="Fill in a template constant, e.g. --var LOCATION_ID=abc123 (repeatable)",
    )
    p.set_defaults(func=cmd_new_connector, framed=True)

    p = sub.add_parser("presets", help="List the connectors that are already written")
    p.set_defaults(func=cmd_presets, framed=True)

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
    p.set_defaults(func=cmd_connect_claude, framed=True)

    p = sub.add_parser("schedule", help="Generate and install a LaunchAgent for periodic `brain sync`")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--interval-minutes", type=float, default=15)
    p.add_argument("--slug", default=None)
    p.add_argument("--load", action="store_true", help="Load it immediately with launchctl")
    p.set_defaults(func=cmd_schedule, framed=True)

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
    p.set_defaults(func=cmd_status, framed=True)

    args = parser.parse_args()
    if getattr(args, "func", None) is None:
        return app.run(None)

    # Draw the box around commands that just print a result. Three kinds are
    # excluded and each for its own reason:
    #   - interactive ones (new, init without a path, secret set) prompt
    #     mid-run, and a framed block shows nothing until it ends;
    #   - sync streams for minutes, where watching it beats a tidy border;
    #   - secret get must stay bare on stdout so it can be piped.
    # Only when stdout is a terminal: piping `brain status` into a file should
    # produce text, not box-drawing characters.
    if getattr(args, "framed", False) and ui.out.is_terminal:
        project = getattr(args, "project", None)
        with ui.framed(args.command, ui.short_path(Path(project).resolve()) if project else None):
            return args.func(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
