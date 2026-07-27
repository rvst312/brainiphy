"""Input helpers for the interactive commands (`brain new`, the folder picker).

Kept apart from ui.py so that module stays output-only, but they share the same
Rich console (ui.out) — a prompt drawn on a different console would not line up
with the styled output around it.

Every helper returns None when the user bails out with Ctrl-C / Ctrl-D, so
callers can treat "cancelled" as an ordinary value instead of catching
KeyboardInterrupt at every call site.
"""
from __future__ import annotations

import os
from pathlib import Path

from rich.prompt import Confirm, Prompt
from rich.text import Text

from brainiphy_cli import ui


def ask(question: str, *, default: str | None = None, suffix: str = ": ") -> str | None:
    """One line of free text. Returns the default when the user just hits enter."""
    asker = Prompt(Text(question, style="brain.step"), console=ui.out)
    asker.prompt_suffix = suffix
    try:
        answer = asker(default=default) if default is not None else asker()
    except (EOFError, KeyboardInterrupt):
        ui.blank()
        return None
    return (answer or "").strip()


def confirm(question: str, *, default: bool = True) -> bool | None:
    try:
        return Confirm.ask(Text(question, style="brain.step"), console=ui.out, default=default)
    except (EOFError, KeyboardInterrupt):
        ui.blank()
        return None


def choose(question: str, options: list[str], *, default: int | None = None) -> int | None:
    """Numbered menu. Returns the 0-based index of the chosen option.

    Re-asks on anything unparseable rather than returning a sentinel: every
    caller in the wizard would otherwise have to re-render the whole step.
    """
    ui.blank()
    ui.out.print(Text(question, style="brain.step"))
    for i, option in enumerate(options, 1):
        line = Text("  ")
        line.append(f"{i}) ", style="brain.hint")
        line.append(option)
        ui.out.print(line)

    default_text = str(default + 1) if default is not None else None
    while True:
        answer = ask("›", default=default_text, suffix=" ")
        if answer is None:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        ui.warn(f"pick a number between 1 and {len(options)}")


def ask_path(question: str, *, must_exist: bool = True) -> Path | None:
    """A filesystem path, with ~ and $VARS expanded. Re-asks if it must exist
    and does not."""
    while True:
        answer = ask(question)
        if answer is None:
            return None
        if not answer:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(answer))).resolve()
        if must_exist and not path.exists():
            ui.warn("no such path:", path)
            continue
        return path
