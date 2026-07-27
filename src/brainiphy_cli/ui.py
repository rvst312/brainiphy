"""Shared Rich console + output helpers for the `brain` CLI.

Every user-facing line in cli.py / sync.py / picker.py goes through here so the
whole tool looks like one program: same icons, same colors, same stdout/stderr
split. Rich disables color on its own when the stream is not a TTY (and honors
NO_COLOR), so piping `brain status` into a file still yields clean text.

Messages are rendered as Text objects, never markup strings — paths and record
titles routinely contain '[' and would otherwise be eaten as markup tags.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

APP_TITLE = "brainiphy"

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
    if _capturing:
        # Inside framed(), the box already carries the title and the project
        # path — printing them again would just be a heading above a heading.
        return
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
    if _capturing:
        # Inside framed(), nothing is on screen until the block ends, so an
        # animation would only write spinner frames into the captured text.
        yield
        return
    with out.status(Text(message, style="brain.step"), spinner="dots"):
        yield


# --------------------------------------------------------------- framing ----
# The menu draws the whole app inside a box. Commands print through the helpers
# above without knowing that, so framed() captures their output and re-renders
# it inside a Panel instead of asking every command to care.

# A Panel costs one border character and one pad character on each side.
_FRAME_CHROME = 4

_capturing = False


def clear() -> None:
    out.clear()


def app_panel(body, *, title: str | None = None, subtitle: str | None = None,
              style: str = "brain.hint") -> Panel:
    """The app's box. One place so every screen has the same chrome."""
    return Panel(
        body,
        title=Text(title or APP_TITLE, style="brain.head"),
        title_align="left",
        subtitle=Text(subtitle, style="brain.info") if subtitle else None,
        subtitle_align="right",
        border_style=style,
        box=ROUNDED,
        padding=(1, 2),
    )


@contextmanager
def framed(title: str, subtitle: str | None = None, *, style: str = "brain.hint"):
    """Run a block and render everything it printed inside the app box.

    Both consoles are captured, so a `ui.error()` on stderr lands inside the
    box with everything else instead of escaping it. The consoles are narrowed
    while capturing, otherwise text is wrapped to the full terminal width and
    then wraps a second time inside the border.

    Not for long-running work: nothing appears until the block finishes. Stream
    those unframed — live progress beats a tidy border.
    """
    global _capturing

    saved_out, saved_err = out.width, err.width
    out.width = err.width = max(20, saved_out - _FRAME_CHROME)
    _capturing = True

    stack = ExitStack()
    captured_out = stack.enter_context(out.capture())
    captured_err = stack.enter_context(err.capture())
    try:
        yield
    finally:
        # Close the captures and restore the terminal before printing, even if
        # the block raised — otherwise a failure prints nothing at all.
        stack.close()
        _capturing = False
        out.width, err.width = saved_out, saved_err

        parts = [chunk.strip("\n") for chunk in (captured_out.get(), captured_err.get()) if chunk.strip()]
        body = Group(*(Text.from_ansi(part) for part in parts)) if parts else Text("(no output)", style="brain.info")
        out.print(app_panel(body, title=title, subtitle=subtitle))
