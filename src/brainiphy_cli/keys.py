"""Single-keypress reading, for the navigable menu.

`prompt.py` reads whole lines — fine for answering a question, useless for
moving a selection with the arrow keys, which needs the terminal in raw mode so
a keystroke arrives without waiting for Enter.

Stdlib only (termios/tty), like everything else here. The terminal settings are
restored in a finally block on every path: leaving a shell in raw mode after a
crash makes it look broken (no echo, no line editing), and that is a much worse
failure than the menu not working.

In raw mode the kernel no longer turns Ctrl-C into SIGINT, so it arrives as the
byte 0x03 and is re-raised as KeyboardInterrupt here — callers keep handling
cancellation the way they already do.
"""
from __future__ import annotations

import os
import select
import sys

UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
ENTER = "enter"
ESC = "esc"
BACKSPACE = "backspace"
UNKNOWN = "unknown"

# An escape byte starts either a key sequence (arrows) or a bare Esc press. The
# only way to tell is whether more bytes follow immediately.
_SEQUENCE_GRACE_SECONDS = 0.05

_ARROWS = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}


def supported() -> bool:
    """False when there is no terminal to put into raw mode (piped, launchd)."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


# A byte read while disambiguating an escape sequence but not consumed by it.
# Without this, pressing Esc and then another key inside the grace window loses
# the second key: it has already been taken off the fd and cannot be un-read.
_pushback: list[int] = []


def _read_byte(fd: int) -> int:
    if _pushback:
        return _pushback.pop(0)
    data = os.read(fd, 1)
    if not data:
        raise EOFError
    return data[0]


def _pending(fd: int) -> bool:
    if _pushback:
        return True
    return bool(select.select([fd], [], [], _SEQUENCE_GRACE_SECONDS)[0])


def read_key() -> str:
    """Block until one keypress, returned as a name (UP/ENTER/…) or a literal
    character. Raises KeyboardInterrupt on Ctrl-C, EOFError on Ctrl-D.

    Reads the file descriptor with os.read rather than sys.stdin. That is not
    incidental: sys.stdin keeps its own userspace buffer, so a whole escape
    sequence arriving at once ends up buffered there, select() on the fd then
    reports nothing pending, and an arrow key is misread as a bare Esc followed
    by two stray characters. Holding an arrow key down does exactly that.
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        # TCSANOW, not tty.setraw's default TCSAFLUSH: flushing would discard
        # whatever is already in the input queue, and the menu re-enters raw
        # mode between every keypress — so anything typed while it repaints
        # would be silently dropped.
        tty.setraw(fd, termios.TCSANOW)
        first = _read_byte(fd)

        if first == 0x03:
            raise KeyboardInterrupt
        if first == 0x04:
            raise EOFError
        if first in (0x0D, 0x0A):
            return ENTER
        if first in (0x7F, 0x08):
            return BACKSPACE

        if first == 0x1B:
            # Peek rather than block, so a bare Esc does not hang waiting for a
            # second byte that is never coming.
            if not _pending(fd):
                return ESC
            second = _read_byte(fd)
            if second != 0x5B:  # 0x5B == '['
                # Esc followed by something else: two keypresses, not one.
                _pushback.append(second)
                return ESC
            return _ARROWS.get(chr(_read_byte(fd)), UNKNOWN)

        if first < 0x80:
            return chr(first)

        # A non-ASCII key (an accented letter on a Spanish layout) arrives as
        # several UTF-8 bytes; take them until they decode.
        buffer = bytes([first])
        while len(buffer) < 4:
            buffer += os.read(fd, 1)
            try:
                return buffer.decode("utf-8")
            except UnicodeDecodeError:
                continue
        return UNKNOWN
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
