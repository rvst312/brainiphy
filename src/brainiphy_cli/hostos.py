"""Which operating system's services brainiphy talks to.

macOS is where brainiphy started: credentials in the Keychain, the sync loop
as a LaunchAgent. On a Linux server neither exists, so the two modules that
touch them (keychain.py, project.py's scheduling) ask here and use a file
store and a systemd timer instead.

`BRAINIPHY_BACKEND=macos|linux` overrides the detection. It exists for the
test suite, which exercises both families on whichever OS CI happens to give
it; nobody should need it in normal use.
"""
from __future__ import annotations

import os
import sys


def is_macos() -> bool:
    forced = os.environ.get("BRAINIPHY_BACKEND", "").strip().lower()
    if forced in ("macos", "linux"):
        return forced == "macos"
    return sys.platform == "darwin"
