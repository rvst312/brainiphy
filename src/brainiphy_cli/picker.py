"""Interactive folder picker: where should a brain live?

The first screen a user meets when they run bare `brain` outside a project, or
`brain init` with no path, so it carries the same chrome as the menu — arrow
keys, one box — rather than looking like a different program.

Two implementations on purpose. The navigable one needs a terminal that can be
put into raw mode; when that is not available (a pty that refuses raw mode, an
unusual terminal) it falls back to the original numbered/typed loop, which
needs nothing but line input. The fallback is not dead code: losing the folder
picker entirely would make `brain init` with no argument unusable.

Returns the chosen path — created on disk only after an explicit confirmation —
or None if cancelled.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.text import Text

from brainiphy_cli import keys, prompt, ui

# Width per grid cell before the typed listing wraps into fewer columns.
_MIN_CELL_WIDTH = 26

# Rows of the folder list visible at once in the navigable picker. Beyond this
# it scrolls, so a home directory with 80 folders still fits on screen.
_VIEWPORT = 12


def default_start_dir() -> Path:
    """Where the browser starts: ~/Documents if it exists (the normal case on
    macOS), otherwise the home directory."""
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def _display(path: Path) -> str:
    return ui.short_path(path)


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except PermissionError:
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def _expand(text: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(text.strip()))).resolve()


def _is_brain(path: Path) -> bool:
    """Already scaffolded. Worth flagging in the listing: picking an existing
    brain and creating a new one are different intentions."""
    return (path / "connectors" / "registry.yaml").exists()


def _looks_like_path(text: str) -> bool:
    return "/" in text or text.startswith("~") or text.startswith("$")


def _resolve_choice(target: Path) -> Path | None:
    """Confirm the destination, creating the folder only if the user says so."""
    if target.exists() and not target.is_dir():
        ui.error("exists and is not a folder:", target)
        return None
    if target.is_dir():
        if not prompt.confirm(f"Use {_display(target)} as the brain?"):
            return None
    else:
        if not prompt.confirm(f"{_display(target)} does not exist. Create it?"):
            return None
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.error("could not create it:", exc)
            return None
    return target


# ------------------------------------------------------------- navigable ----

def _listing_body(current: Path, dirs: list[Path], cursor: int, offset: int) -> Text:
    body = Text()

    if not dirs:
        body.append("  (no subfolders here)\n", style="brain.info")
    else:
        window = dirs[offset : offset + _VIEWPORT]
        if offset:
            body.append(f"   ↑ {offset} more above\n", style="brain.info")
        for index, folder in enumerate(window, start=offset):
            selected = index == cursor
            body.append(" ❯ " if selected else "   ", style="brain.hint")
            body.append(f" {folder.name}/ ", style="reverse bold" if selected else "brain.path")
            if _is_brain(folder):
                body.append("  already a brain", style="brain.ok")
            body.append("\n")
        remaining = len(dirs) - (offset + len(window))
        if remaining > 0:
            body.append(f"   ↓ {remaining} more below\n", style="brain.info")

    # Two lines rather than one: movement and actions are different kinds of
    # key, and a single row wraps mid-hint on an 80-column terminal.
    body.append("\n")
    for row in (
        (("↑↓", "move"), ("→", "open"), ("←", "up")),
        (("a", "use this folder"), ("n", "new"), ("/", "type a path"), ("q", "cancel")),
    ):
        for key_label, meaning in row:
            body.append(f"  {key_label}", style="brain.hint")
            body.append(f" {meaning}", style="brain.info")
        body.append("\n")
    return body


def _pick_navigable(start: Path) -> Path | None:
    current = start
    cursor = 0
    offset = 0

    while True:
        dirs = _subdirs(current)
        cursor = max(0, min(cursor, len(dirs) - 1)) if dirs else 0
        # Keep the cursor inside the visible window.
        offset = min(offset, cursor)
        if cursor >= offset + _VIEWPORT:
            offset = cursor - _VIEWPORT + 1

        ui.clear()
        subtitle = _display(current) + ("   (already a brain)" if _is_brain(current) else "")
        ui.out.print(ui.app_panel(
            _listing_body(current, dirs, cursor, offset),
            title="where should the brain live?",
            subtitle=subtitle,
        ))

        try:
            key = keys.read_key()
        except (KeyboardInterrupt, EOFError):
            return None

        if key == keys.UP and dirs:
            cursor = (cursor - 1) % len(dirs)
            offset = min(offset, cursor)
            continue
        if key == keys.DOWN and dirs:
            cursor = (cursor + 1) % len(dirs)
            continue
        if key in (keys.RIGHT, keys.ENTER) and dirs:
            current, cursor, offset = dirs[cursor], 0, 0
            continue
        if key in (keys.LEFT, "u") and current.parent != current:
            # Land on the folder just left, so going up and down again is not
            # a hunt through an alphabetical list.
            previous, current = current, current.parent
            siblings = _subdirs(current)
            cursor = siblings.index(previous) if previous in siblings else 0
            offset = max(0, cursor - _VIEWPORT + 1)
            continue
        if key in (keys.ESC, "q"):
            return None

        if key == "a":
            chosen = _resolve_choice(current)
            if chosen:
                return chosen
            continue
        if key == "n":
            name = prompt.ask(f"Folder name (inside {_display(current)})")
            if name:
                chosen = _resolve_choice(current / name)
                if chosen:
                    return chosen
            continue
        if key == "/":
            typed = prompt.ask("Path")
            if typed:
                chosen = _resolve_choice(_expand(typed))
                if chosen:
                    return chosen
            continue


# ----------------------------------------------------------------- typed ----

def _render_typed_listing(current: Path, dirs: list[Path]) -> None:
    ui.blank()
    ui.out.print(Text("📂 " + _display(current), style="brain.head"))

    if not dirs:
        ui.out.print(Text("   (no subfolders)", style="brain.info"))
    else:
        width = len(str(len(dirs)))
        cells = []
        for i, d in enumerate(dirs, 1):
            cell = Text()
            cell.append(f"{str(i).rjust(width)}) ", style="brain.step")
            cell.append(d.name + "/", style="brain.path")
            cells.append(cell)
        columns = max(1, min(4, ui.out.width // _MIN_CELL_WIDTH))
        grid = ui.table(*[""] * columns)
        # Row-major fill: numbering reads left-to-right, the way it is typed.
        for start in range(0, len(cells), columns):
            row = cells[start : start + columns]
            grid.add_row(*(row + [""] * (columns - len(row))))
        ui.print_table(grid)

    keys_line = Text()
    keys_line.append("   n", style="brain.hint")
    keys_line.append(" new folder here   ", style="brain.info")
    keys_line.append("a", style="brain.hint")
    keys_line.append(" use this one   ", style="brain.info")
    if current.parent != current:
        keys_line.append("u", style="brain.hint")
        keys_line.append(" up   ", style="brain.info")
    keys_line.append("q", style="brain.hint")
    keys_line.append(" cancel", style="brain.info")
    ui.out.print(keys_line)


def _pick_typed(start: Path) -> Path | None:
    current = start

    ui.header("Where do you want to create the brain?")
    ui.out.print(Text("Pick a number to enter a folder, or type/paste a path.", style="brain.info"))

    while True:
        dirs = _subdirs(current)
        _render_typed_listing(current, dirs)

        choice = prompt.ask("›", suffix=" ")
        if choice is None or choice.lower() == "q":
            return None
        if not choice:
            continue

        if choice.isdigit() and 1 <= int(choice) <= len(dirs):
            current = dirs[int(choice) - 1]
            continue

        low = choice.lower()
        if low == "u" and current.parent != current:
            current = current.parent
            continue

        if low == "a":
            chosen = _resolve_choice(current)
            if chosen:
                return chosen
            continue

        if low == "n":
            name = prompt.ask(f"Folder name (inside {_display(current)})")
            if not name:
                continue
            chosen = _resolve_choice(current / name)
            if chosen:
                return chosen
            continue

        if _looks_like_path(choice):
            chosen = _resolve_choice(_expand(choice))
            if chosen:
                return chosen
            continue

        # Free text: filter by name so long lists don't have to be counted through.
        matches = [d for d in dirs if low in d.name.lower()]
        if len(matches) == 1:
            current = matches[0]
        elif matches:
            ui.info("several match: " + ", ".join(d.name for d in matches))
        else:
            ui.warn(f"did not understand '{choice}' — use a number, n/a/u/q, or a path with '/'")


# ------------------------------------------------------------------- api ----

def pick_project_dir(start: Path | None = None) -> Path | None:
    current = (start or default_start_dir()).resolve()
    if keys.supported():
        return _pick_navigable(current)
    return _pick_typed(current)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
