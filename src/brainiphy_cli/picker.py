"""Interactive folder picker for `brain init` with no arguments.

A Rich-rendered directory browser: lists numbered subfolders in a grid, lets you
move up/down the tree, create a new folder, or paste/type a path directly.
Returns the chosen path (not created on disk unless the user asks for it) or
None if cancelled.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from rich.prompt import Confirm, Prompt
from rich.text import Text

from brainiphy_cli import ui

# Width per grid cell before the listing wraps into fewer columns.
_MIN_CELL_WIDTH = 26


# Where the browser starts when nothing else is given: ~/Documents if it exists
# (the normal case on macOS), otherwise the home directory.
def default_start_dir() -> Path:
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def _display(path: Path) -> str:
    """Readable path, with ~ instead of the home directory."""
    return ui.short_path(path)


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except PermissionError:
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def _expand(text: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(text.strip()))).resolve()


def _ask(prompt: str, suffix: str = ": ") -> str | None:
    """Read one line; None if the user bails out with Ctrl-C / Ctrl-D."""
    asker = Prompt(Text(prompt, style="brain.step"), console=ui.out)
    asker.prompt_suffix = suffix
    try:
        return asker().strip()
    except (EOFError, KeyboardInterrupt):
        ui.blank()
        return None


def _confirm(prompt: str) -> bool:
    try:
        return Confirm.ask(Text(prompt, style="brain.step"), console=ui.out, default=True)
    except (EOFError, KeyboardInterrupt):
        ui.blank()
        return False


def _looks_like_path(text: str) -> bool:
    return "/" in text or text.startswith("~") or text.startswith("$")


def _render_listing(current: Path, dirs: list[Path]) -> None:
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

    keys = Text()
    keys.append("   n", style="brain.hint")
    keys.append(" new folder here   ", style="brain.info")
    keys.append("a", style="brain.hint")
    keys.append(" use this one   ", style="brain.info")
    if current.parent != current:
        keys.append("u", style="brain.hint")
        keys.append(" up   ", style="brain.info")
    keys.append("q", style="brain.hint")
    keys.append(" cancel", style="brain.info")
    ui.out.print(keys)


def _resolve_choice(target: Path) -> Path | None:
    """Confirm the final destination; create the folder if it does not exist yet."""
    if target.exists() and not target.is_dir():
        ui.error("exists and is not a folder:", target)
        return None
    if target.is_dir():
        if not _confirm(f"Use {_display(target)} as the brain?"):
            return None
    else:
        if not _confirm(f"{_display(target)} does not exist. Create it?"):
            return None
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ui.error("could not create it:", exc)
            return None
    return target


def pick_project_dir(start: Path | None = None) -> Path | None:
    """Interactive browser. Returns the chosen folder, or None if cancelled."""
    current = (start or default_start_dir()).resolve()

    ui.header("Where do you want to create the brain?")
    ui.out.print(Text("Pick a number to enter a folder, or type/paste a path.", style="brain.info"))

    while True:
        dirs = _subdirs(current)
        _render_listing(current, dirs)

        choice = _ask("›", suffix=" ")
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
            name = _ask(f"Folder name (inside {_display(current)})")
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
    # unreachable


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
