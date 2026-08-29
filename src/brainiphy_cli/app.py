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
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from rich.text import Text

from brainiphy_cli import (
    actions,
    brains,
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


def _choose(title: str, options: list[tuple[str, ...]], subtitle: str | None = None,
            intro: str | None = None) -> int | None:
    """A list screen in the app's chrome. Returns an index, or None if backed out.

    An option is (label, hint) or (label, hint, badge). The badge stays visible
    on every row rather than only the selected one: "needs code" is exactly the
    thing you want to know *before* choosing, not after.
    """
    cursor = 0
    while True:
        ui.clear()
        body = Text()
        if intro:
            _append_wrapped(body, intro, "brain.info", indent=2)
            body.append("\n")
        for index, option in enumerate(options):
            label, hint = option[0], option[1]
            badge = option[2] if len(option) > 2 else ""
            selected = index == cursor
            body.append(" ❯ " if selected else "   ", style="brain.hint")
            body.append(f" {label} ", style="reverse bold" if selected else "brain.head")
            if badge:
                body.append(f"  {badge}", style="brain.ok" if badge == "ready to run" else "brain.warn")
            body.append("\n")
            if selected and hint:
                _append_wrapped(body, hint, "brain.info", indent=6)
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


# The source kinds, each labelled by what it *is* rather than by a sentence
# about it — "local folder" and "http api" are the words a person already has in
# their head, and they match the `type` recorded in registry.yaml, so the label
# you picked is the label you see everywhere afterwards. The badge says up front
# whether choosing this leaves you with a working connector or with homework.
SOURCE_KINDS = [
    ("local folder", "a folder on this Mac — mirrored into the brain on every sync",
     "ready to run", actions.add_local_folder),
    ("preset", "a system brainiphy already ships a finished connector for",
     "ready to run", actions.add_preset),
    ("http api", "a REST API — retries, pagination and scopes handled; you write the endpoints",
     "needs code", actions.add_api),
    ("url", "a public web page, fetched once by graphify", "one-off", actions.add_url),
    ("custom", "anything else — a database, a local export: a bare connector",
     "needs code", actions.add_custom),
]


def _registered_summary(project: Path) -> str:
    """'crm (gohighlevel), docs (local folder)' — what is already feeding this
    brain, shown on the picker so adding a source visibly lands somewhere."""
    entries = project_mod.load_registry_entries(project)
    if not entries:
        return "nothing feeds this brain yet"
    described = ", ".join(
        f"{e.get('name', '?')} ({project_mod.type_label(e, project)})" for e in entries
    )
    return f"already feeding it: {described}"


def _do_sources(project: Path) -> None:
    """Loop, so adding three sources is three answers rather than three trips
    back through the flow."""
    while True:
        subtitle = ui.short_path(project)
        options: list[tuple[str, ...]] = [(k[0], k[1], k[2]) for k in SOURCE_KINDS]
        options.append(("Done — back to the checklist", "you can always come back for another", ""))

        picked = _choose("what feeds this brain?", options, subtitle,
                         intro=_registered_summary(project))
        if picked is None or picked == len(options) - 1:
            return

        handler = SOURCE_KINDS[picked][3]
        ui.clear()
        ui.out.print(ui.app_panel(
            Text(f"Adding a {SOURCE_KINDS[picked][0]} source. Answer the questions below —\n"
                 "Ctrl-C at any prompt backs out without creating anything.",
                 style="brain.info"),
            title="add a data source", subtitle=subtitle))
        try:
            handler(project)
        except actions.Cancelled:
            ui.warn("cancelled — nothing was created")
        _pause("press any key to go back to the source list")


def _open_editor(script: Path) -> None:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        with ui.framed("no editor configured", ui.short_path(script.parent)):
            ui.warn("$EDITOR is not set, so there is nothing to open it with")
            ui.hint("set one (e.g. in ~/.zshrc):", "export EDITOR=nano")
            ui.hint("or edit it yourself:", str(script))
        _pause()
        return
    # Outside a framed block and outside raw mode: the editor owns the terminal
    # while it runs, and capturing its output would show a blank screen.
    ui.clear()
    subprocess.run(shlex.split(editor) + [str(script)])


def _run_probe(script: Path) -> None:
    """Hit the real API and report what the credential can read, writing
    nothing. The fastest way to find out whether a connector is finished."""
    out_dir = Path(tempfile.mkdtemp(prefix="brain-probe-"))
    ui.clear()
    ui.header("probe", script.parent.name)
    ui.info("Calls the API read-only and reports which objects the credential can see.")
    ui.blank()
    # sys.executable, not the script's shebang: `brain` may well be installed
    # under an interpreter that `env python3` does not resolve to, and then
    # every connector fails on `import brainiphy_cli`. sync.py runs them the
    # same way for the same reason.
    result = subprocess.run([sys.executable, str(script), "--out", str(out_dir), "--probe"],
                            capture_output=True, text=True)
    ui.raw(result.stdout)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("the probe failed — the connector is not finished yet")
    shutil.rmtree(out_dir, ignore_errors=True)
    _pause()


def _do_implement(project: Path) -> None:
    """Mostly human work, but not a screen you can only read: pick a connector
    and it opens in your editor, or probes the API to show what is still
    missing. It used to print two commands and leave you there."""
    while True:
        pending = steps._pending_connectors(project)
        if not pending:
            with ui.framed("finish the connectors", ui.short_path(project)):
                ui.ok("every registered connector is ready to run")
                ui.info("nothing to do here — carry on to the sync")
            _pause()
            return

        options: list[tuple[str, ...]] = [
            (name, f"connectors/{name}/sync.py", why) for name, why in pending
        ]
        options.append(("Done — back to the checklist", "unfinished connectors are skipped by sync", ""))
        picked = _choose("which connector needs finishing?", options, ui.short_path(project),
                         intro="These are registered but cannot run yet.")
        if picked is None or picked == len(options) - 1:
            return

        name, why = pending[picked]
        script = project / "connectors" / name / "sync.py"
        can_probe = "collect.run" in script.read_text(encoding="utf-8", errors="ignore")

        todo: list[tuple[str, ...]] = [("Open it in $EDITOR", f"{why} — fix it in {script.name}", "")]
        if can_probe:
            todo.append(("Probe the API", "read-only: reports what the credential can see", ""))
        todo.append(("Back", "pick another connector", ""))

        action = _choose(f"{name}: {why}", todo, ui.short_path(project))
        if action is None or action == len(todo) - 1:
            continue
        if action == 0:
            _open_editor(script)
        else:
            _run_probe(script)


def _do_build(project: Path, *, full: bool = True) -> None:
    # Unframed on purpose: a sync runs connectors and a graph rebuild, and
    # watching it happen beats a tidy border around silence.
    ui.clear()
    ui.header("first sync" if full else "sync", project)
    ui.info("Pulls every source in and indexes it. Documents need an LLM backend —")
    ui.info("your Claude Code subscription is used when no API key is set.")
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
            try:
                keychain.set_secret(item, value)
            except keychain.SecretWriteError as exc:
                ui.error(str(exc))
            else:
                ui.ok("stored in the Keychain:", item)
        else:
            ui.warn("empty value, nothing stored")
    _pause()


def _tool_view(project: Path) -> None:
    with ui.framed("the graph", ui.short_path(project)):
        project_mod.open_graph(project)
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
    ("View the graph", "open the interactive picture in your browser", _tool_view),
]


def _open_tools(project: Path) -> None:
    """Operations that are not steps: sync, credentials, presets, the graph.

    Switching brains used to live here as "Change project". It is `b` on the
    checklist now, because the app opens on the list of brains, and going back
    to that list is navigation rather than a tool.
    """
    picked = _choose("tools", [(label, hint) for label, hint, _ in TOOLS], ui.short_path(project))
    if picked is None:
        return
    TOOLS[picked][2](project)


# ------------------------------------------------------------------ home ----

_INDENT = 8


def _append_wrapped(body: Text, text: str, style: str, indent: int = _INDENT) -> None:
    """Add an indented paragraph that keeps its indent when it wraps.

    Rich wraps to the panel width but starts continuation lines at column 0,
    which makes a step's explanation collide with the list above it. Wrap it
    here instead, against the width actually left inside the box.
    """
    width = max(20, ui.out.width - ui.FRAME_CHROME - indent)
    for line in textwrap.wrap(text, width=width) or [""]:
        body.append(" " * indent + line + "\n", style=style)


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


def _prepare(project: Path) -> None:
    """Scaffold a freshly chosen folder before the checklist appears.

    Choosing the folder and preparing it are one intention, not two: a person
    who just answered "put the brain here" has not asked to be shown a
    'Scaffold the project' checkbox before they can add their first source. It
    is idempotent, it only writes registry.yaml and the two ignore files, and it
    prints what it did — visible, not behind their back. Step 2 stays in the
    checklist for `brain init` and for re-running it.
    """
    registry = project / "connectors" / "registry.yaml"
    ignore = project / ".graphifyignore"
    if registry.exists() and ignore.exists() and "connectors/" in ignore.read_text(
        encoding="utf-8", errors="ignore"
    ):
        return
    with ui.framed("preparing the folder", ui.short_path(project)):
        project_mod.scaffold(project)
    _pause("press any key to start adding sources")


BACK, QUIT = "back", "quit"


def _brain_screen(project: Path) -> str:
    """The seven-step checklist for one brain. Returns BACK or QUIT."""
    state = steps.inspect(project)
    # Open on whatever this brain needs next, not on step 1.
    cursor = state.steps.index(state.next_step) if state.next_step else 0

    while True:
        ui.clear()
        ui.out.print(ui.app_panel(
            _home_body(state, cursor),
            title=project.name,
            subtitle=f"{state.done_count}/{len(state.steps)}  ·  {ui.short_path(project)}",
        ))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            ui.blank()
            return QUIT

        if key == keys.UP:
            cursor = (cursor - 1) % len(state.steps)
            continue
        if key == keys.DOWN:
            cursor = (cursor + 1) % len(state.steps)
            continue
        if key in (keys.ESC, keys.LEFT, "b", "B"):
            return BACK
        if key in ("q", "Q"):
            ui.blank()
            return QUIT
        if key in ("t", "T"):
            _open_tools(project)
            state = steps.inspect(project)
            cursor = min(cursor, len(state.steps) - 1)
            continue
        if key in ("v", "V"):
            _tool_view(project)
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
        if state.done_count > previous_done and state.next_step is not None:
            cursor = state.steps.index(state.next_step)
        else:
            cursor = min(cursor, len(state.steps) - 1)


# ----------------------------------------------------------- your brains ----

def _new_brain() -> Path | None:
    """Choose a folder for a new brain and prepare it."""
    chosen = picker.pick_project_dir()
    if chosen is None:
        return None
    _prepare(chosen)
    return chosen


def _add_existing_brain() -> Path | None:
    """Register a brain that already exists somewhere on this machine."""
    chosen = picker.pick_project_dir(
        title="add an existing brain", purpose="brain", allow_new=False)
    if chosen is None:
        return None
    with ui.framed("adding a brain", ui.short_path(chosen)):
        if not brains.is_brain(chosen):
            ui.warn("that folder is not a brain yet")
            ui.info("preparing it now — this only writes registry.yaml and two ignore files")
            project_mod.scaffold(chosen)
        elif brains.remember(chosen):
            ui.ok("added to your brains:", chosen.name)
        else:
            ui.info("already in your brains:", chosen.name)
    _pause()
    return chosen


def _forget_brain(summary) -> None:
    """Drop a brain from the list, after saying plainly what that does.

    The fear this screen exists to answer is "am I about to delete a client's
    data", so it answers it before asking anything.
    """
    ui.clear()
    body = Text()
    _append_wrapped(body, f"Remove '{summary.name}' from this list?", "brain.head", indent=2)
    body.append("\n")
    _append_wrapped(body, "The list is the only thing that changes. Every file stays "
                          "where it is — the connectors, the graph, the mirrored "
                          "documents — and you can add it back at any time.",
                    "brain.info", indent=2)
    body.append("\n")
    _append_wrapped(body, str(summary.path), "brain.path", indent=2)
    body.append("\n  y", style="brain.hint")
    body.append(" remove it from the list   ", style="brain.info")
    body.append("n", style="brain.hint")
    body.append(" keep it", style="brain.info")
    ui.out.print(ui.app_panel(body, title="remove from the list"))
    try:
        key = keys.read_key()
    except (KeyboardInterrupt, EOFError):
        return
    if key in ("y", "Y"):
        with ui.framed("removed", summary.name):
            brains.forget(summary.path)
            ui.ok("removed from your brains:", summary.name)
            ui.info("its files are untouched at", summary.path)
        _pause()


def _brains_body(summaries, cursor: int) -> Text:
    body = Text()
    _append_wrapped(body, "Each brain is a folder with its own sources and its own "
                          "graph. Open one to carry on setting it up.",
                    "brain.info", indent=2)
    body.append("\n")

    for index, summary in enumerate(summaries):
        selected = index == cursor
        body.append(" ❯ " if selected else "   ", style="brain.hint")
        body.append(f" {summary.name} ", style="reverse bold" if selected else "brain.head")

        if summary.missing:
            body.append("  moved or deleted", style="brain.err")
        elif summary.complete:
            body.append("  ready", style="brain.ok")
        else:
            body.append(f"  {summary.steps_done}/{summary.steps_total} set up",
                        style="brain.warn")
        body.append("\n")

        if selected:
            if summary.missing:
                _append_wrapped(body, "This folder is no longer there. Removing it from "
                                      "the list is all that is left to do.",
                                "brain.info", indent=6)
            else:
                sources = (f"{summary.sources} source{'' if summary.sources == 1 else 's'}"
                           if summary.sources else "no sources yet")
                if summary.pending_sources:
                    sources += f", {summary.pending_sources} still unfinished"
                graph = (f"{summary.nodes} nodes" if summary.nodes is not None
                         else "graph not built yet")
                _append_wrapped(
                    body,
                    f"{sources}  ·  {graph}  ·  synced {brains.ago(summary.last_sync)}",
                    "brain.info", indent=6)
                _append_wrapped(body, "in " + ui.short_path(summary.path.parent),
                                "brain.info", indent=6)

    body.append("\n  ↑↓", style="brain.hint")
    body.append(" move   ", style="brain.info")
    body.append("↵", style="brain.hint")
    body.append(" open   ", style="brain.info")
    body.append("n", style="brain.hint")
    body.append(" new   ", style="brain.info")
    body.append("a", style="brain.hint")
    body.append(" add existing   ", style="brain.info")
    body.append("v", style="brain.hint")
    body.append(" graph   ", style="brain.info")
    body.append("d", style="brain.hint")
    body.append(" remove   ", style="brain.info")
    body.append("q", style="brain.hint")
    body.append(" quit", style="brain.info")
    return body


def _brains_screen() -> int:
    """The app's front door: every brain on this machine."""
    cursor = 0
    while True:
        summaries = brains.summaries()

        if not summaries:
            # An empty list is not a screen worth showing: there is exactly one
            # thing anybody can do from it, so do that instead.
            project = _new_brain()
            if project is None:
                ui.info("no folder chosen — nothing was created")
                ui.hint("run it again any time with:", "brain")
                return 0
            if _brain_screen(project) == QUIT:
                return 0
            continue

        cursor = min(cursor, len(summaries) - 1)
        ui.clear()
        ui.out.print(ui.app_panel(
            _brains_body(summaries, cursor),
            title="your brains",
            subtitle=f"{len(summaries)} brain{'' if len(summaries) == 1 else 's'}",
        ))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            ui.blank()
            return 0

        if key == keys.UP:
            cursor = (cursor - 1) % len(summaries)
        elif key == keys.DOWN:
            cursor = (cursor + 1) % len(summaries)
        elif key in (keys.ESC, "q", "Q"):
            ui.blank()
            return 0
        elif key in ("n", "N"):
            project = _new_brain()
            if project is not None and _brain_screen(project) == QUIT:
                return 0
        elif key in ("a", "A"):
            project = _add_existing_brain()
            if project is not None and _brain_screen(project) == QUIT:
                return 0
        elif key in ("d", "D"):
            _forget_brain(summaries[cursor])
        elif key in ("v", "V"):
            if not summaries[cursor].missing:
                _tool_view(summaries[cursor].path)
        elif key in (keys.ENTER, keys.RIGHT):
            summary = summaries[cursor]
            # A missing brain has one useful action and it is not "open".
            if summary.missing:
                _forget_brain(summary)
            elif _brain_screen(summary.path) == QUIT:
                return 0
        elif key.isdigit() and 1 <= int(key) <= len(summaries):
            cursor = int(key) - 1


def run(project_arg: str | None = None) -> int:
    if not (picker.is_interactive() and keys.supported()):
        ui.error("`brain` needs a terminal — it walks you through the setup")
        ui.hint("in a script or an agent turn, use the individual commands:",
                "brain init … && brain new-connector … && brain sync …")
        return 1

    # Named a folder, or standing in one: that is the brain they mean, and the
    # list would only be a screen in the way of it.
    if project_arg is not None:
        project = Path(project_arg).expanduser().resolve()
        project.mkdir(parents=True, exist_ok=True)
        _prepare(project)
        brains.remember(project)
        _brain_screen(project)
        return 0

    cwd = Path.cwd()
    if brains.is_brain(cwd):
        brains.remember(cwd)
        if _brain_screen(cwd) == QUIT:
            return 0

    return _brains_screen()
