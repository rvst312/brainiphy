"""`brain` with no arguments — the navigable menu.

The CLI grew a command per operation, which is right for scripting and for an
agent, and wrong for a human who has to remember that adding a GoHighLevel
source is `new-connector --preset gohighlevel --var LOCATION_ID=…`. This module
is the other front door: arrow keys, one screen, the whole app inside a box.

It owns no operations. Every entry here calls the same project.py / sync.py /
wizard.py function the equivalent command calls, so the menu cannot drift away
from `brain new-connector` the way a second implementation would. What it adds
is discovery: which actions exist, which one this brain needs next, and what
each one is for.

Needs a terminal (it reads single keypresses and repaints). Piped or run from
launchd it refuses and points at the individual commands, the same way
`brain new` does.
"""
from __future__ import annotations

import getpass
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rich.text import Text

from brainiphy_cli import (
    keychain,
    keys,
    picker,
    presets,
    project as project_mod,
    prompt,
    steps,
    sync as sync_mod,
    ui,
    wizard,
)


@dataclass
class Item:
    shortcut: str
    label: str
    hint: str
    action: Callable[[Path], None] | None = None
    # Which step in steps.py this advances, so the menu can point at whichever
    # one the project actually needs next instead of a fixed "start here".
    step_key: str | None = None


# --------------------------------------------------------------- helpers ----

def _secret_item(project: Path, connector: str) -> str:
    """The Keychain item a connector actually reads.

    Normally the convention project.py generates, but a connector may have been
    given a different SECRET_ITEM (a brain rebuilt under a new name, a shared
    credential). Read it from the file rather than assume, so "manage
    credentials" writes the item the script will look up.
    """
    script = project / "connectors" / connector / "sync.py"
    if script.exists():
        match = re.search(r"""^SECRET_ITEM = ["'](.+?)["']$""",
                          script.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
        if match and "REPLACE_ME" not in match.group(1):
            return match.group(1)
    return project_mod.secret_item_name(project, connector)


def _pause() -> None:
    ui.out.print(Text("\n  press any key to go back", style="brain.info"))
    try:
        keys.read_key()
    except (KeyboardInterrupt, EOFError):
        pass


def _connector_names(project: Path) -> list[str]:
    return [e["name"] for e in project_mod.load_registry_entries(project) if e.get("name")]


# ----------------------------------------------------------------- screen ---

def _menu_body(project: Path, state: steps.BrainState, items: list[Item], cursor: int) -> Text:
    """The menu itself: a progress line, then the actions with one selected."""
    body = Text()

    next_step = state.next_step
    body.append(f"{state.done_count}/{len(state.steps)} steps done", style="brain.head")
    if next_step is not None:
        body.append("   next: ", style="brain.info")
        body.append(next_step.title, style="brain.warn")
    else:
        body.append("   fully set up", style="brain.ok")
    body.append("\n\n")

    for index, item in enumerate(items):
        selected = index == cursor
        # The chevron does the work on a terminal without colour; the reverse
        # style is the belt to its braces.
        body.append(" ❯ " if selected else "   ", style="brain.hint")
        body.append(f"{item.shortcut}  ", style="brain.step" if not selected else "brain.hint")
        label_style = "reverse bold" if selected else "brain.head"
        body.append(f" {item.label} ", style=label_style)
        if next_step is not None and item.step_key == next_step.key:
            body.append("  ← next", style="brain.warn")
        body.append("\n")
        if selected and item.hint:
            body.append(f"      {item.hint}\n", style="brain.info")

    body.append("\n")
    body.append("  ↑↓", style="brain.hint")
    body.append(" move   ", style="brain.info")
    body.append("↵", style="brain.hint")
    body.append(" choose   ", style="brain.info")
    body.append("shortcut key", style="brain.hint")
    body.append(" jump   ", style="brain.info")
    body.append("q", style="brain.hint")
    body.append(" quit", style="brain.info")
    return body


def _choose_from(title: str, options: list[tuple[str, str]], subtitle: str | None = None) -> int | None:
    """A one-off list screen, same look as the main menu. Returns an index or
    None if the user backed out."""
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
        elif key in (keys.ESC, "q"):
            return None
        elif key.isdigit() and 1 <= int(key) <= len(options):
            return int(key) - 1


# ---------------------------------------------------------------- actions ---

def _act_status(project: Path) -> None:
    with ui.framed("status", ui.short_path(project)):
        steps.render_status(project)
    _pause()


def _act_guide(project: Path) -> None:
    with ui.framed("the 7 steps", ui.short_path(project)):
        steps.render(steps.inspect(project), verbose=True)
    _pause()


def _act_add_source(project: Path) -> None:
    options = [
        ("A system brainiphy already knows", "ready-made connector — " + ", ".join(presets.names())),
        ("A folder on this Mac", "mirrored automatically, nothing to write"),
        ("A public URL", "fetched by graphify directly"),
        ("A REST API", "plumbing done, you write the endpoints"),
        ("Something else", "a bare connector to fill in"),
    ]
    picked = _choose_from("add a data source", options, ui.short_path(project))
    if picked is None:
        return

    # The wizard's handlers, not copies of them: same prompts, same generated
    # files, same registry entry as `brain new`.
    handler = (wizard.add_preset, wizard.add_local_folder, wizard.add_url,
               wizard.add_api, wizard.add_custom)[picked]
    ui.clear()
    ui.out.print(ui.app_panel(Text("Answer the questions below.", style="brain.info"),
                              title="add a data source", subtitle=ui.short_path(project)))
    try:
        handler(project)
    except wizard.Cancelled:
        ui.warn("cancelled")
    _pause()


def _act_sync(project: Path, *, full: bool = False) -> None:
    # Deliberately unframed: a sync runs connectors and a graph rebuild, and
    # watching it happen matters more than a tidy border around it.
    ui.clear()
    ui.header("sync" + (" (full rebuild)" if full else ""), project)
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


def _act_full_sync(project: Path) -> None:
    _act_sync(project, full=True)


def _act_claude(project: Path) -> None:
    options = [
        ("Claude Code only", "CLAUDE.md + hooks in this project — the lower-risk default"),
        ("Also register in Claude Desktop", "adds an MCP server pointing at the graph"),
        ("Desktop + trust this folder", "also appends it to localAgentModeTrustedFolders"),
    ]
    picked = _choose_from("connect to Claude", options, ui.short_path(project))
    if picked is None:
        return
    with ui.framed("connect to Claude", ui.short_path(project)):
        project_mod.connect_claude(project, desktop=picked >= 1, trust_desktop=picked == 2)
    _pause()


def _act_schedule(project: Path) -> None:
    if not project_mod.load_registry_entries(project):
        with ui.framed("schedule", ui.short_path(project)):
            ui.error("no connectors registered — nothing worth scheduling yet")
        _pause()
        return

    ui.clear()
    ui.out.print(ui.app_panel(Text("How often should the background sync run?", style="brain.info"),
                              title="schedule", subtitle=ui.short_path(project)))
    answer = prompt.ask("Interval in minutes", default="15")
    if answer is None:
        return
    try:
        interval = float(answer)
    except ValueError:
        ui.error("not a number:", answer)
        _pause()
        return

    with ui.framed("schedule", ui.short_path(project)):
        project_mod.schedule(project, interval_minutes=interval, load=True)
    _pause()


def _act_secrets(project: Path) -> None:
    names = _connector_names(project)
    if not names:
        with ui.framed("credentials", ui.short_path(project)):
            ui.info("no connectors registered yet, so nothing needs a credential")
        _pause()
        return

    options = [(name, f"stored as {_secret_item(project, name)}") for name in names]
    picked = _choose_from("which connector's credential?", options, ui.short_path(project))
    if picked is None:
        return

    item = _secret_item(project, names[picked])
    ui.clear()
    ui.out.print(ui.app_panel(
        Text(f"The value is stored in the macOS Keychain as '{item}'.\n"
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


def _act_presets(project: Path) -> None:
    with ui.framed("available presets", ui.short_path(project)):
        table = ui.table("preset", "system", "pulls")
        for name in presets.names():
            preset = presets.PRESETS[name]
            table.add_row(ui.cell(name, "brain.path"), ui.cell(preset.title),
                          ui.cell(preset.description))
        ui.print_table(table)
        ui.blank()
        ui.info("install one from 'Add a data source'")
    _pause()


# ------------------------------------------------------------------- run ----

MENU: list[Item] = [
    Item("1", "Status", "connectors, graph size, and what this brain needs next",
         _act_status),
    Item("2", "Add a data source", "a preset, a folder, a URL or an API",
         _act_add_source, step_key="sources"),
    Item("3", "Sync now", "run the connectors that are due, then rebuild the graph",
         _act_sync, step_key="build"),
    Item("4", "Full rebuild", "re-index everything, including documents (needs an LLM key)",
         _act_full_sync),
    Item("5", "Connect to Claude", "wire the graph into Claude Code, and optionally Desktop",
         _act_claude, step_key="claude"),
    Item("6", "Schedule syncing", "a LaunchAgent that keeps the graph fresh on its own",
         _act_schedule, step_key="schedule"),
    Item("7", "Credentials", "store a connector's token in the macOS Keychain",
         _act_secrets),
    Item("8", "Browse presets", "connectors that are already written for you",
         _act_presets),
    Item("9", "The 7 steps", "the whole process, and how far along this one is",
         _act_guide),
    Item("p", "Change project", "point the menu at a different brain"),
    Item("q", "Quit", ""),
]


def _resolve_project(project_arg: str | None) -> Path | None:
    if project_arg is not None:
        target = Path(project_arg).expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        return target

    # A brain in the current directory is almost always the one meant.
    cwd = Path.cwd()
    if (cwd / "connectors" / "registry.yaml").exists():
        return cwd

    chosen = picker.pick_project_dir()
    if chosen is None:
        return None
    project_mod.scaffold(chosen)
    return chosen


def run(project_arg: str | None = None) -> int:
    if not (picker.is_interactive() and keys.supported()):
        ui.error("the menu needs a terminal — it reads single keypresses")
        ui.hint("in a script or an agent turn, use the individual commands:",
                "brain init … && brain new-connector … && brain sync …")
        return 1

    project = _resolve_project(project_arg)
    if project is None:
        return 1

    cursor = 0
    while True:
        state = steps.inspect(project)
        ui.clear()
        ui.out.print(ui.app_panel(
            _menu_body(project, state, MENU, cursor),
            subtitle=ui.short_path(project),
        ))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            ui.blank()
            return 0

        if key == keys.UP:
            cursor = (cursor - 1) % len(MENU)
            continue
        if key == keys.DOWN:
            cursor = (cursor + 1) % len(MENU)
            continue

        if key == keys.ENTER:
            item = MENU[cursor]
        elif key in (keys.ESC, "q", "Q"):
            ui.blank()
            return 0
        else:
            matched = next((i for i in MENU if i.shortcut == key.lower()), None)
            if matched is None:
                continue
            item = matched
            cursor = MENU.index(item)

        if item.shortcut == "q":
            ui.blank()
            return 0
        if item.shortcut == "p":
            chosen = _resolve_project(None)
            if chosen is not None:
                project = chosen
            continue

        try:
            item.action(project)
        except KeyboardInterrupt:
            # Ctrl-C inside an action goes back to the menu, not out of the app.
            ui.blank()
            ui.warn("cancelled")
