"""Shared Rich console + output helpers for the `brain` CLI.

Every user-facing line in cli.py / sync.py / picker.py goes through here so the
whole tool looks like one program: same icons, same colors, same stdout/stderr
split. Rich disables color on its own when the stream is not a TTY (and honors
NO_COLOR), so piping `brain status` into a file still yields clean text.

Messages are rendered as Text objects, never markup strings — paths and record
titles routinely contain '[' and would otherwise be eaten as markup tags.
"""
from __future__ import annotations

from contextlib import contextmanager

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

THEME = Theme(
    {
        "brain.step": "bold cyan",
        "brain.ok": "bold green",
        "brain.info": "dim",
        "brain.warn": "yellow",
        "brain.err": "bold red",
        "brain.path": "cyan",
        "brain.hint": "magenta",
        "brain.head": "bold white",
    }
)

# highlight=False: Rich's auto-highlighter colors numbers/paths inside plain
# strings, which fights with the explicit styling below.
out = Console(theme=THEME, highlight=False)
err = Console(theme=THEME, highlight=False, stderr=True)


def _line(icon: str, icon_style: str, message: str, detail=None, *, style: str = "", console: Console | None = None) -> None:
    text = Text()
    text.append(f"{icon} ", style=icon_style)
    text.append(message, style=style)
    if detail is not None:
        text.append(" ")
        text.append(str(detail), style="brain.path")
    # soft_wrap so a long path is wrapped by the terminal, not hard-broken into
    # the output itself — otherwise piping to a file splits the path in two.
    (console or out).print(text, soft_wrap=True)


def step(message: str, detail=None) -> None:
    """Something is about to happen / is happening."""
    _line("→", "brain.step", message, detail)


def ok(message: str, detail=None) -> None:
    """Something was created or completed."""
    _line("✓", "brain.ok", message, detail)


def info(message: str, detail=None) -> None:
    """Nothing to do, state already fine — deliberately quiet."""
    _line("·", "brain.info", message, detail, style="brain.info")


def warn(message: str, detail=None) -> None:
    _line("!", "brain.warn", message, detail, style="brain.warn")


def error(message: str, detail=None) -> None:
    """Failures go to stderr so `brain sync` stays pipeable."""
    _line("✗", "brain.err", message, detail, style="brain.err", console=err)


def hint(message: str, command: str | None = None) -> None:
    """A suggested next command for the user to run."""
    text = Text()
    text.append("↳ ", style="brain.hint")
    text.append(message, style="brain.info")
    out.print(text)
    if command:
        # Same reason as _line: a wrapped command must stay one copy-pasteable line.
        out.print(Text("    " + command, style="bold"), soft_wrap=True)


def short_path(value) -> str:
    """Home-relative display (~/clients/acme) when possible — full paths are
    long enough to wrap the header on a normal terminal."""
    from pathlib import Path

    try:
        return "~/" + str(Path(str(value)).relative_to(Path.home()))
    except ValueError:
        return str(value)


def header(title: str, subtitle=None) -> None:
    out.print()
    out.print(Text(title, style="brain.head"))
    if subtitle is not None:
        # Own line, not appended to the title: a project path is routinely wider
        # than the terminal and would drag the title along when it wraps.
        out.print(Text(short_path(subtitle), style="brain.path"))
    out.rule(style="brain.info")


def blank() -> None:
    out.print()


def raw(text: str, *, stderr: bool = False) -> None:
    """Verbatim output from a subprocess (graphify, a connector script).

    Printed without markup or highlighting so nothing in it is reinterpreted.
    """
    text = text.rstrip()
    if not text:
        return
    # soft_wrap: let the terminal wrap long lines instead of Rich hard-breaking
    # them — tracebacks and graphify output stay copy-pasteable.
    (err if stderr else out).print(
        text, markup=False, highlight=False, soft_wrap=True, style="brain.info" if not stderr else "brain.err"
    )


def table(*columns: str, title: str | None = None, show_header: bool = True) -> Table:
    t = Table(
        title=title,
        title_style="brain.head",
        header_style="brain.step",
        border_style="brain.info",
        box=None,
        pad_edge=False,
        show_header=show_header and any(columns),
    )
    for column in columns:
        t.add_column(column)
    return t


def cell(value, style: str = "") -> Text:
    """Table cell that never gets reinterpreted as markup (connector names and
    paths come from user-written files and may contain '[')."""
    return Text(str(value), style=style)


def print_table(t: Table) -> None:
    out.print(t)


def panel(body, title: str | None = None, style: str = "brain.info") -> None:
    out.print(Panel(body, title=title, title_align="left", border_style=style, padding=(0, 1)))


@contextmanager
def working(message: str):
    """Spinner for a long-running child process. No-ops on a non-TTY."""
    with out.status(Text(message, style="brain.step"), spinner="dots"):
        yield
