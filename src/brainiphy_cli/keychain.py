#!/usr/bin/env python3
"""Credential storage for brainiphy connectors, backed by the macOS Keychain.

Tokens never touch disk in plain text and never pass through a chat/agent context.
The user (a human) registers each credential once, out of band:

    security add-generic-password -a "$USER" -s <item-name> -w '<the-secret-value>'

Connector scripts then call get_secret("<item-name>") at run time.
"""
from __future__ import annotations

import subprocess


class SecretNotFoundError(RuntimeError):
    pass


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
            f"  security add-generic-password -a \"$USER\" -s {item} -w '<value>'"
        )
    return result.stdout.strip()


def set_secret(item: str, value: str) -> None:
    """Create or update a Keychain item. Intended for setup scripts/tests, not
    for connector sync scripts (which should only ever read)."""
    subprocess.run(
        ["security", "add-generic-password", "-a", __import__("os").environ["USER"],
         "-s", item, "-w", value, "-U"],
        check=True,
        capture_output=True,
    )


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
