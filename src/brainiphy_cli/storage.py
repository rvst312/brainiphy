"""Small filesystem boundaries shared by generated connectors and the CLI."""
from __future__ import annotations

import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_lock(path: Path):
    """Serialize writers on macOS/Linux; never wait indefinitely for a job."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another operation is already writing here: {path.parent}") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise ValueError("name must start with a letter or digit and contain only letters, digits, _ or -")
    return name


def contained_path(root: Path, *parts: str) -> Path:
    candidate = root.joinpath(*parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path escapes {root}: {candidate}")
    return candidate


def atomic_write(path: Path, text: str) -> None:
    """Replace a complete file, never expose a truncated write to readers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".brainiphy-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
