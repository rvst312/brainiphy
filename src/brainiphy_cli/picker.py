"""Interactive folder picker for `brain init` with no arguments.

A minimal directory browser over stdin/stdout (no dependencies): lists numbered
subfolders, lets you move up/down the tree, create a new folder, or paste/type a
path directly. Returns the chosen path (not created on disk unless the user asks
for it) or None if cancelled.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Where the browser starts when nothing else is given: ~/Documents if it exists
# (the normal case on macOS), otherwise the home directory.
def default_start_dir() -> Path:
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


def _display(path: Path) -> str:
    """Readable path, with ~ instead of the home directory."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _subdirs(path: Path) -> list[Path]:
    try:
        entries = [p for p in path.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except PermissionError:
        return []
    return sorted(entries, key=lambda p: p.name.lower())


def _expand(text: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(text.strip()))).resolve()


def _ask(prompt: str) -> str | None:
    """Read one line; None if the user bails out with Ctrl-C / Ctrl-D."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _confirm(prompt: str) -> bool:
    answer = _ask(f"{prompt} [Y/n]: ")
    return answer is not None and answer.lower() in ("", "y", "yes")


def _looks_like_path(text: str) -> bool:
    return "/" in text or text.startswith("~") or text.startswith("$")


def _resolve_choice(target: Path) -> Path | None:
    """Confirm the final destination; create the folder if it does not exist yet."""
    if target.exists() and not target.is_dir():
        print(f"  ! {target} exists and is not a folder")
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
            print(f"  ! could not create it: {exc}")
            return None
    return target


def pick_project_dir(start: Path | None = None) -> Path | None:
    """Interactive browser. Returns the chosen folder, or None if cancelled."""
    current = (start or default_start_dir()).resolve()

    print("\nWhere do you want to create the brain?")
    print("Pick a number to enter a folder, or type/paste a path.\n")

    while True:
        dirs = _subdirs(current)
        print(f"📂 {_display(current)}")
        if dirs:
            width = len(str(len(dirs)))
            for i, d in enumerate(dirs, 1):
                print(f"  {str(i).rjust(width)}) {d.name}/")
        else:
            print("  (no subfolders)")
        print("\n  n) create a new folder here")
        print("  a) use this folder as-is")
        if current.parent != current:
            print("  u) go up to " + _display(current.parent))
        print("  q) cancel")

        choice = _ask("\n> ")
        print()
        if choice is None or choice.lower() == "q":
            return None

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
            name = _ask(f"Folder name (inside {_display(current)}): ")
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
            print("Several match:")
            for d in matches:
                print(f"  - {d.name}/")
            print()
        else:
            print(f"Did not understand '{choice}'. Use a number, n/a/u/q, or a path with '/'.\n")
    # unreachable


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
