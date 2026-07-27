"""The `brain` app: the seven steps, walked inside the CLI.

The home screen *is* the process. steps.py already knows what building a brain
consists of and how far along a project is; this renders that as a checklist,
runs whichever step you are on, then re-inspects and moves to the next one. You
never have to know what comes after what — which was the whole problem with a
CLI that had grown one command per operation.

Design rules worth keeping:
  - This module owns no operations. Each step's action calls the same
    project.py / sync.py / actions.py function the equivalent named command
    calls, so the guided path and the scriptable path cannot drift.
  - The step list is not written here. It comes from steps.inspect(), so adding
    or reordering a step is still a single edit in steps.py — the flow follows
    automatically. STEP_ACTIONS only says *how* to perform a step, keyed by the
    same `key` steps.py uses.
  - Steps stay reachable out of order. A brain being set up for the first time
    wants the sequence; a brain six months old wants "add one more source", and
    forcing it back through the flow to get there would be worse than the menu
    this replaced.

Needs a terminal: it reads single keypresses and repaints. Piped or launchd-run
it refuses and points at the individual commands.
"""
from __future__ import annotations

import getpass
import re
import textwrap
from pathlib import Path

from rich.text import Text

from brainiphy_cli import (
    actions,
    keychain,
    keys,
    picker,
    presets,
    project as project_mod,
    prompt,
    steps,
    sync as sync_mod,
    ui,
)


# --------------------------------------------------------------- helpers ----

def _secret_item(project: Path, connector: str) -> str:
    """The Keychain item a connector actually reads.

    Normally the convention project.py generates, but a connector may carry a
    different SECRET_ITEM (a brain rebuilt under a new name, a shared
    credential). Read it from the file rather than assume, so storing a
    credential writes the item the script will look up.
    """
    script = project / "connectors" / connector / "sync.py"
    if script.exists():
        match = re.search(r"""^SECRET_ITEM = ["'](.+?)["']$""",
                          script.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
        if match and "REPLACE_ME" not in match.group(1):
            return match.group(1)
    return project_mod.secret_item_name(project, connector)


def _pause(message: str = "press any key to go back") -> None:
    ui.out.print(Text(f"\n  {message}", style="brain.info"))
    try:
        keys.read_key()
    except (KeyboardInterrupt, EOFError):
        pass


def _choose(title: str, options: list[tuple[str, str]], subtitle: str | None = None) -> int | None:
    """A list screen in the app's chrome. Returns an index, or None if backed out."""
    cursor = 0
    while True:
        ui.clear()
        body = Text()
        for index, (label, hint) in enumerate(options):
            selected = index == cursor
            body.append(" ❯ " if selected else "   ", style="brain.hint")
            body.append(f" {label} ", style="reverse bold" if selected else "brain.head")
            body.append("\n")
            if selected and hint:
                body.append(f"      {hint}\n", style="brain.info")
        body.append("\n  ↑↓", style="brain.hint")
        body.append(" move   ", style="brain.info")
        body.append("↵", style="brain.hint")
        body.append(" choose   ", style="brain.info")
        body.append("esc", style="brain.hint")
        body.append(" back", style="brain.info")
        ui.out.print(ui.app_panel(body, title=title, subtitle=subtitle))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            return None

        if key == keys.UP:
            cursor = (cursor - 1) % len(options)
        elif key == keys.DOWN:
            cursor = (cursor + 1) % len(options)
        elif key == keys.ENTER:
            return cursor
        elif key in (keys.ESC, keys.LEFT, "q"):
            return None
        elif key.isdigit() and 1 <= int(key) <= len(options):
            return int(key) - 1


# ---------------------------------------------------------- step actions ----
# One per steps.py key. Each returns nothing; the caller re-inspects the project
# afterwards, so a step that half-succeeded simply stays the current step.

def _do_graphify(project: Path) -> None:
    # Unframed: it may prompt to install, and a framed block shows nothing
    # until it ends, so the question would be invisible.
    ui.clear()
    ui.header("install graphify", project)
    actions.ensure_graphify()
    _pause()


def _do_scaffold(project: Path) -> None:
    with ui.framed("prepare the folder", ui.short_path(project)):
        ui.info("Creates connectors/registry.yaml and the ignore files. Safe to re-run.")
        ui.blank()
        project_mod.scaffold(project)
    _pause()


SOURCE_KINDS = [
    ("A system brainiphy already knows", "ready-made connector, just a couple of answers"),
    ("A folder on this Mac", "mirrored automatically, nothing to write"),
    ("A public URL", "fetched by graphify directly"),
    ("A REST API", "plumbing done, you write the endpoints"),
    ("Something else", "a bare connector to fill in"),
]


def _do_sources(project: Path) -> None:
    """Loop, so adding three sources is three answers rather than three trips
    back through the flow."""
    while True:
        registered = [e.get("name", "?") for e in project_mod.load_registry_entries(project)]
        subtitle = ui.short_path(project)
        options = list(SOURCE_KINDS)
        options.append(("Done adding sources",
                        f"registered: {', '.join(registered)}" if registered else "none yet"))

        picked = _choose("what feeds this brain?", options, subtitle)
        if picked is None or picked == len(options) - 1:
            return

        handler = (actions.add_preset, actions.add_local_folder, actions.add_url,
                   actions.add_api, actions.add_custom)[picked]
        ui.clear()
        ui.out.print(ui.app_panel(Text("Answer the questions below.", style="brain.info"),
                                  title="add a data source", subtitle=subtitle))
        try:
            handler(project)
        except actions.Cancelled:
            ui.warn("cancelled")
        _pause("press any key to add another, or pick 'Done' next")


def _do_implement(project: Path) -> None:
    """Nothing to run here — this step is human work. Say precisely what each
    connector is missing and give the exact command to go fix it."""
    pending = steps._pending_connectors(project)
    with ui.framed("finish the connectors", ui.short_path(project)):
        if not pending:
            ui.ok("every registered connector is ready to run")
        else:
            for name, why in pending:
                ui.warn(f"{name}: {why}")
                script = project / "connectors" / name / "sync.py"
                ui.hint("edit:", f"$EDITOR {script}")
                if why.startswith("needs ") and why != "needs code":
                    ui.hint("then check what it can read:", f"{script} --out /tmp/probe --probe")
    _pause()


def _do_build(project: Path, *, full: bool = True) -> None:
    # Unframed on purpose: a sync runs connectors and a graph rebuild, and
    # watching it happen beats a tidy border around silence.
    ui.clear()
    ui.header("first sync" if full else "sync", project)
    ui.info("Pulls every source in and indexes it. Documents need an LLM backend —")
    ui.info("an API key, or Claude Code running /graphify in the folder.")
    ui.blank()

    pending = steps.unimplemented_connectors(project)
    if pending:
        ui.warn("these cannot run yet and will be skipped: " + ", ".join(pending))
        ui.blank()

    if not prompt.confirm("Run it now?", default=True):
        return
    try:
        report = sync_mod.run(project, full=full)
    except FileNotFoundError as exc:
        ui.error(str(exc))
    else:
        ui.blank()
        if report.errors:
            ui.error("finished with errors: " + ", ".join(report.errors))
        else:
            ui.ok("done")
    _pause()


def _do_claude(project: Path) -> None:
    options = [
        ("Claude Code only", "CLAUDE.md + hooks here — the lower-risk default"),
        ("Also register in Claude Desktop", "adds an MCP server pointing at the graph"),
        ("Desktop + trust this folder", "also appends it to localAgentModeTrustedFolders"),
    ]
    picked = _choose("connect it to Claude", options, ui.short_path(project))
    if picked is None:
        return
    with ui.framed("connect it to Claude", ui.short_path(project)):
        project_mod.connect_claude(project, desktop=picked >= 1, trust_desktop=picked == 2)
    _pause()


def _do_schedule(project: Path) -> None:
    if not project_mod.load_registry_entries(project):
        with ui.framed("keep it in sync", ui.short_path(project)):
            ui.error("no connectors registered — nothing worth scheduling yet")
        _pause()
        return

    ui.clear()
    ui.out.print(ui.app_panel(
        Text("A LaunchAgent re-runs `brain sync` in the background so the graph\n"
             "does not go stale.", style="brain.info"),
        title="keep it in sync", subtitle=ui.short_path(project)))
    answer = prompt.ask("How often, in minutes?", default="15")
    if answer is None:
        return
    try:
        interval = float(answer)
    except ValueError:
        ui.error("not a number:", answer)
        _pause()
        return

    with ui.framed("keep it in sync", ui.short_path(project)):
        project_mod.schedule(project, interval_minutes=interval, load=True)
    _pause()


STEP_ACTIONS = {
    "graphify": _do_graphify,
    "scaffold": _do_scaffold,
    "sources": _do_sources,
    "implement": _do_implement,
    "build": _do_build,
    "claude": _do_claude,
    "schedule": _do_schedule,
}


# ----------------------------------------------------------------- tools ----

def _tool_sync(project: Path) -> None:
    _do_build(project, full=False)


def _tool_full_rebuild(project: Path) -> None:
    _do_build(project, full=True)


def _tool_status(project: Path) -> None:
    with ui.framed("status", ui.short_path(project)):
        steps.render_status(project)
    _pause()


def _tool_credentials(project: Path) -> None:
    names = [e["name"] for e in project_mod.load_registry_entries(project) if e.get("name")]
    if not names:
        with ui.framed("credentials", ui.short_path(project)):
            ui.info("no connectors registered yet, so nothing needs a credential")
        _pause()
        return

    picked = _choose("which connector's credential?",
                     [(n, f"stored as {_secret_item(project, n)}") for n in names],
                     ui.short_path(project))
    if picked is None:
        return

    item = _secret_item(project, names[picked])
    ui.clear()
    ui.out.print(ui.app_panel(
        Text(f"Stored in the macOS Keychain as '{item}'.\n"
             "Input is hidden and it never touches a file or a chat.", style="brain.info"),
        title="set a credential", subtitle=ui.short_path(project)))
    try:
        value = getpass.getpass(f"Value for {item} (hidden): ")
    except (EOFError, KeyboardInterrupt):
        return
    with ui.framed("credentials", ui.short_path(project)):
        if value:
            keychain.set_secret(item, value)
            ui.ok("stored in the Keychain:", item)
        else:
            ui.warn("empty value, nothing stored")
    _pause()


def _tool_presets(project: Path) -> None:
    with ui.framed("available presets", ui.short_path(project)):
        table = ui.table("preset", "system", "pulls")
        for name in presets.names():
            preset = presets.PRESETS[name]
            table.add_row(ui.cell(name, "brain.path"), ui.cell(preset.title),
                          ui.cell(preset.description))
        ui.print_table(table)
        ui.blank()
        ui.info("install one from step 3, 'Add data sources'")
    _pause()


TOOLS = [
    ("Sync now", "run the connectors that are due", _tool_sync),
    ("Full rebuild", "re-index everything, documents included", _tool_full_rebuild),
    ("Status", "connectors, graph size, due times", _tool_status),
    ("Credentials", "store a connector's token in the Keychain", _tool_credentials),
    ("Browse presets", "connectors that are already written", _tool_presets),
    ("Change project", "point the app at a different brain", None),
]


def _open_tools(project: Path) -> Path | None:
    """Returns a new project when the user switched, else None."""
    picked = _choose("tools", [(label, hint) for label, hint, _ in TOOLS], ui.short_path(project))
    if picked is None:
        return None
    _, _, action = TOOLS[picked]
    if action is None:
        return _resolve_project(None)
    action(project)
    return None


# ------------------------------------------------------------------ home ----

_INDENT = 8


def _append_wrapped(body: Text, text: str, style: str) -> None:
    """Add an indented paragraph that keeps its indent when it wraps.

    Rich wraps to the panel width but starts continuation lines at column 0,
    which makes a step's explanation collide with the step list above it. Wrap
    it here instead, against the width actually left inside the box.
    """
    width = max(20, ui.out.width - ui.FRAME_CHROME - _INDENT)
    for line in textwrap.wrap(text, width=width) or [""]:
        body.append(" " * _INDENT + line + "\n", style=style)


def _home_body(state: steps.BrainState, cursor: int) -> Text:
    body = Text()

    for index, step in enumerate(state.steps):
        selected = index == cursor
        if selected and index:
            body.append("\n")
        icon, icon_style = (
            ("–", "brain.info") if step.state == steps.SKIP
            else ("✓", "brain.ok") if step.done
            else ("○", "brain.info")
        )
        body.append(" ❯ " if selected else "   ", style="brain.hint")
        body.append(icon, style="brain.hint" if selected else icon_style)
        body.append(f" {step.number}  ", style="brain.info")
        body.append(f" {step.title} ", style="reverse bold" if selected else
                    ("brain.info" if step.done else "brain.head"))
        body.append("\n")

        if selected:
            if step.why:
                _append_wrapped(body, step.why, "brain.info")
            if step.detail:
                _append_wrapped(body, step.detail, "brain.info")
            body.append(" " * _INDENT + "↵ ", style="brain.hint")
            body.append("do this now\n" if not step.done else "do it again\n", style="brain.warn")
            body.append("\n")

    body.append("\n")
    if state.complete:
        body.append("  this brain is fully set up\n", style="brain.ok")
    body.append("  ↑↓", style="brain.hint")
    body.append(" move   ", style="brain.info")
    body.append("↵", style="brain.hint")
    body.append(" run this step   ", style="brain.info")
    body.append("t", style="brain.hint")
    body.append(" tools   ", style="brain.info")
    body.append("q", style="brain.hint")
    body.append(" quit", style="brain.info")
    return body


def _resolve_project(project_arg: str | None) -> Path | None:
    if project_arg is not None:
        target = Path(project_arg).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    # A brain in the current directory is almost always the one meant.
    cwd = Path.cwd()
    if (cwd / "connectors" / "registry.yaml").exists():
        return cwd
    # Not scaffolded here: pick a folder, and let step 2 do the scaffolding —
    # the flow should perform its own steps, not have them happen behind it.
    return picker.pick_project_dir()


def run(project_arg: str | None = None) -> int:
    if not (picker.is_interactive() and keys.supported()):
        ui.error("`brain` needs a terminal — it walks you through the setup")
        ui.hint("in a script or an agent turn, use the individual commands:",
                "brain init … && brain new-connector … && brain sync …")
        return 1

    project = _resolve_project(project_arg)
    if project is None:
        return 1

    state = steps.inspect(project)
    # Open on whatever this brain needs next, not on step 1.
    cursor = state.steps.index(state.next_step) if state.next_step else 0

    while True:
        ui.clear()
        ui.out.print(ui.app_panel(
            _home_body(state, cursor),
            subtitle=f"{state.done_count}/{len(state.steps)}  ·  {ui.short_path(project)}",
        ))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            ui.blank()
            return 0

        if key == keys.UP:
            cursor = (cursor - 1) % len(state.steps)
            continue
        if key == keys.DOWN:
            cursor = (cursor + 1) % len(state.steps)
            continue
        if key in (keys.ESC, "q", "Q"):
            ui.blank()
            return 0
        if key in ("t", "T"):
            switched = _open_tools(project)
            if switched is not None:
                project = switched
            state = steps.inspect(project)
            cursor = min(cursor, len(state.steps) - 1)
            continue
        if key.isdigit() and 1 <= int(key) <= len(state.steps):
            cursor = int(key) - 1
            continue
        if key != keys.ENTER:
            continue

        action = STEP_ACTIONS.get(state.steps[cursor].key)
        if action is None:
            continue
        try:
            action(project)
        except KeyboardInterrupt:
            # Ctrl-C inside a step returns to the checklist, not out of the app.
            ui.blank()
            ui.warn("cancelled")
        except actions.Cancelled:
            pass

        # Re-inspect and advance: the point of the flow is that finishing a step
        # moves you on without having to work out what came after it.
        previous_done = state.done_count
        state = steps.inspect(project)
        if state.next_step is not None and state.done_count > previous_done:
            cursor = state.steps.index(state.next_step)

    return 0
