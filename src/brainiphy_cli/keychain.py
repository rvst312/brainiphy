#!/usr/bin/env python3
"""Credential storage for brainiphy connectors: the macOS Keychain, or on Linux
a folder of owner-only files (see "file store" below).

Tokens never touch disk in plain text and never pass through a chat/agent
context. The user (a human) registers each credential once, out of band:

    security add-generic-password -a "$USER" -s <item-name> -U -w

Note the bare `-w` at the end. `security` then reads the value from stdin
instead of taking it as an argument, which is the whole point: an argument is
readable by any process running as the same user for as long as the command
runs, and it is also what lands in shell history and in launchd logs. `security`
says so itself — "Use of the -p or -w options is insecure. Specify -w as the
last option to be prompted."

Connector scripts then call get_secret("<item-name>") at run time. They must
never call set_secret(): a connector reads a credential, it does not store one.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
from pathlib import Path

from brainiphy_cli import hostos


class SecretNotFoundError(RuntimeError):
    pass


class SecretWriteError(RuntimeError):
    """A credential could not be stored, and nothing was saved under that name.

    Its own class because the caller has to be able to tell the user that the
    value did not make it — `security` has exit paths that return 0 having
    stored nothing, or having stored something else.
    """


# ------------------------------------------------------------ file store ---
#
# Linux has no Keychain, and a server has no desktop keyring either (no
# session, no D-Bus). There a credential is one file per item, readable only by
# its owner, in a directory readable only by its owner. Put that directory on
# an encrypted volume and the guarantee is the same as on macOS: nothing in
# plain text on a disk somebody else can read, nothing in argv or a log.
#
# BRAINIPHY_SECRETS_DIR selects the directory (and opts a Mac into the file
# store too, which is what a brain on an encrypted external disk wants).

def secrets_dir():
    """Where file-backed credentials live, or None when the Keychain is used."""
    explicit = os.environ.get("BRAINIPHY_SECRETS_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if hostos.is_macos():
        return None
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "brainiphy" / "secrets"


def store_label() -> str:
    """What to call the store in messages: the Keychain, or the folder."""
    folder = secrets_dir()
    return "the Keychain" if folder is None else str(folder)


def _secret_file(folder: Path, item: str) -> Path:
    # The item name becomes a file name, so it must not be able to leave the
    # folder or hide in it.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item or ""):
        raise ValueError(f"not a valid credential name: {item!r}")
    return folder / item


def _file_get(folder: Path, item: str) -> str:
    try:
        path = _secret_file(folder, item)
        mode = path.stat().st_mode
        if mode & 0o077:
            raise SecretNotFoundError(
                f"{path} is readable by other users; refusing to use it. Fix with:\n"
                f"  chmod 600 {path}")
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        raise SecretNotFoundError(
            f"No credential named {item!r} in {folder}. Register it once with:\n"
            f"  brain secret set {item}") from exc


def _file_set(folder: Path, item: str, value: str) -> None:
    try:
        path = _secret_file(folder, item)
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, value.strip().encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        raise SecretWriteError(f"could not store {item!r}: {exc}") from exc
    if _file_get(folder, item) != value.strip():
        raise SecretWriteError(
            f"{item!r} was stored with a different value than the one given")


def get_secret(item: str) -> str:
    """Read a generic password from the macOS Keychain by service name.

    Raises SecretNotFoundError with the exact command to register it, rather
    than letting a raw non-zero-exit CalledProcessError surface to whoever
    wrote the connector.
    """
    folder = secrets_dir()
    if folder is not None:
        return _file_get(folder, item)
    result = subprocess.run(
        ["security", "find-generic-password", "-s", item, "-w"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SecretNotFoundError(
            f"No Keychain item named {item!r}. Register it once with:\n"
            f"  brain secret set {item}\n"
            f"or directly (the bare -w prompts, so the value stays out of your "
            f"shell history):\n"
            f"  security add-generic-password -a \"$USER\" -s {item} -U -w"
        )
    return result.stdout.strip()


def set_secret(item: str, value: str) -> None:
    """Create or update a Keychain item, without the value ever being an argument.

    `security` reads the password from stdin when -w is given last with no
    value, asking for it twice (the value, then a confirmation). That is the
    only way to store one here: process arguments are visible to other
    processes running as the same user for as long as the command runs, and a
    secret in argv is exactly the leak this module exists to prevent. The
    module used to pass `-w <value>` and so contradicted its own rule.

    Raises SecretWriteError rather than returning quietly, because `security`
    does not reliably report this failure itself — see the read-back below.

    For setup (`brain secret set`, the app's credentials screen), never for a
    connector: a connector reads a credential, it does not store one.
    """
    if not value:
        raise SecretWriteError("refusing to store an empty value")
    if "\n" in value or "\r" in value:
        # The value and its confirmation are two lines on stdin, so a value
        # containing a line break would arrive as two different answers and be
        # rejected — see below for why that would not be reported as an error.
        # A pasted token with a stray newline is the realistic case.
        raise SecretWriteError(
            f"{item!r}: a credential containing a line break cannot be stored. "
            "Check for a stray newline in a pasted token.")

    folder = secrets_dir()
    if folder is not None:
        _file_set(folder, item, value)
        return

    result = subprocess.run(
        ["security", "add-generic-password", "-a", getpass.getuser(),
         "-s", item, "-U", "-w"],
        input=f"{value}\n{value}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SecretWriteError(f"could not store {item!r}: {detail}")

    # The exit code is not enough. `security` exits 0 on paths that store
    # nothing, and on at least one that stores something other than what was
    # sent (feed it a value and a confirmation that disagree and watch it
    # succeed). Reporting a credential as saved when it is not moves the
    # failure to the connector's next run, where it reads as an auth problem.
    # So: read it back.
    try:
        stored = get_secret(item)
    except SecretNotFoundError as exc:
        raise SecretWriteError(
            f"{item!r} was not stored — `security` reported success but the "
            f"item does not exist") from exc
    # get_secret() strips surrounding whitespace, so compare against a value
    # that has been through the same treatment; a token with a leading or
    # trailing space cannot round-trip through this store either way.
    if stored != value.strip():
        raise SecretWriteError(
            f"{item!r} was stored with a different value than the one given")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: keychain.py <keychain-item-name>", file=sys.stderr)
        sys.exit(2)
    try:
        print(get_secret(sys.argv[1]))
    except SecretNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
