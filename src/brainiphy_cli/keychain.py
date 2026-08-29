#!/usr/bin/env python3
"""Credential storage for brainiphy connectors, backed by the macOS Keychain.

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
import subprocess


class SecretNotFoundError(RuntimeError):
    pass


class SecretWriteError(RuntimeError):
    """A credential could not be stored, and nothing was saved under that name.

    Its own class because the caller has to be able to tell the user that the
    value did not make it — `security` has exit paths that return 0 having
    stored nothing, or having stored something else.
    """


def get_secret(item: str) -> str:
    """Read a generic password from the macOS Keychain by service name.

    Raises SecretNotFoundError with the exact command to register it, rather
    than letting a raw non-zero-exit CalledProcessError surface to whoever
    wrote the connector.
    """
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
