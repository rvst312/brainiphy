"""The interactive operations a step of the flow performs.

Not a flow of its own: app.py decides *when* each of these runs and what comes
next, this module knows *how* to do one thing while talking to a human. It was
`brain new`'s wizard until the flow moved into the app; the step ordering went
with it, the operations stayed here.

Everything raises Cancelled when the user hits Ctrl-C/Ctrl-D at a prompt, so a
caller unwinds in one place instead of checking a return value at every step.
Each function calls the same project.py helper the equivalent named command
does — the guided path must not grow its own copy of an operation.
"""
from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path

from rich.text import Text

from brainiphy_cli import (
    keychain,
    picker,
    presets,
    project as project_mod,
    prompt,
    steps,
    sync as sync_mod,
    ui,
)

class Cancelled(Exception):
    """The user hit Ctrl-C / Ctrl-D at a prompt. Unwinds to run()."""


def _why(text: str) -> None:
    """One dim line explaining what a step is for — the thing the CLI never
    used to say out loud."""
    ui.out.print(Text(text, style="brain.info"), soft_wrap=True)


def _ask(*args, **kwargs):
    value = prompt.ask(*args, **kwargs)
    if value is None:
        raise Cancelled
    return value


def _confirm(*args, **kwargs) -> bool:
    value = prompt.confirm(*args, **kwargs)
    if value is None:
        raise Cancelled
    return value


def _choose(*args, **kwargs) -> int:
    value = prompt.choose(*args, **kwargs)
    if value is None:
        raise Cancelled
    return value


def _ask_path(*args, **kwargs) -> Path:
    value = prompt.ask_path(*args, **kwargs)
    if value is None:
        raise Cancelled
    return value


def _ask_interval(default: float) -> float:
    while True:
        answer = _ask("How often should it re-sync, in minutes?", default=f"{default:g}")
        try:
            minutes = float(answer)
        except ValueError:
            ui.warn("that is not a number")
            continue
        if minutes <= 0:
            ui.warn("must be greater than 0")
            continue
        return minutes


def ensure_graphify() -> None:
    _why("graphify is the engine that indexes the collected files and builds the graph.")
    try:
        ui.ok("graphify found:", project_mod.find_exe("graphify"))
        return
    except FileNotFoundError:
        ui.warn("graphify is not installed")

    if not _confirm("Install it now with pip3 install --user graphifyy?"):
        ui.hint("install it later with:", "pip3 install --user graphifyy")
        ui.warn("without it, the graph cannot be built — the rest of this still works")
        return

    with ui.working("pip3 install --user graphifyy"):
        result = subprocess.run(
            ["pip3", "install", "--user", "graphifyy"], capture_output=True, text=True
        )
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("install failed — do it by hand, then come back to this step")
        return
    ui.ok("graphify installed")


def add_local_folder(project: Path) -> str | None:
    _why("The folder is mirrored into the brain on every sync — nothing to write.")
    # Browse rather than demand a pasted path: this is the same picker that
    # chose the project folder, and a person adding a client's Drive mirror
    # rarely has its path on the clipboard. `/` inside it still takes a typed
    # path, so pasting is not lost.
    if picker.is_interactive():
        source = picker.pick_project_dir(
            start=Path.home(),
            title="which folder feeds this brain?",
            purpose="source",
            allow_new=False,
        )
    else:
        source = _ask_path("Which folder? (paste the path)")
    if source is None:
        raise Cancelled
    if not source.is_dir():
        ui.error("that is a file, not a folder:", source)
        return None
    default_name = project_mod.slug(source.name)
    name = project_mod.slug(_ask("Name this source", default=default_name))
    interval = _ask_interval(60)

    script = project_mod.create_connector(project, name, interval_minutes=interval, mirror=source)
    if script is None:
        return None
    ui.ok(f"'{name}' is ready — it mirrors that folder, nothing to fill in")
    return name


def add_url(project: Path) -> str | None:
    _why("Fetched once, right now — a URL is a snapshot, not a connector that re-syncs.")
    url = _ask("Which URL?")
    if not url:
        return None
    try:
        graphify = project_mod.find_exe("graphify")
    except FileNotFoundError:
        # Don't stop at "graphify is not installed" — that is step 1, and being
        # sent back to it with no way to act was one of the dead ends.
        ui.warn("graphify is not installed, and it is what fetches the URL")
        ensure_graphify()
        try:
            graphify = project_mod.find_exe("graphify")
        except FileNotFoundError:
            ui.error("still not installed — install it, then add the URL again")
            return None

    with ui.working(f"graphify add {url}"):
        result = subprocess.run([graphify, "add", url], cwd=project, capture_output=True, text=True)
    ui.raw(result.stdout)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("graphify add failed")
        return None
    # graphify add does not rebuild the graph, so this content only lands in it
    # at step 5 — which always runs, hence no update here.
    ui.ok("fetched into the project")
    ui.info("it will not appear in the connector list — nothing re-fetches it. "
            "Add the URL again to refresh it.")
    return None


def _store_secret(project: Path, name: str, label: str) -> None:
    """Offer to put a connector's credential in the Keychain now.

    Always the same item name the connector reads and `brain secret set`
    writes, so the three never drift apart.
    """
    item = project_mod.secret_item_name(project, name)
    _why(f"It will be stored in the macOS Keychain as '{item}' — never in a file, never in chat.")
    if not _confirm(f"Enter the {label} now?", default=True):
        ui.hint("store it later with:", f"brain secret set {item}")
        return
    try:
        value = getpass.getpass(f"Value for {item} (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        raise Cancelled
    if value:
        keychain.set_secret(item, value)
        ui.ok("stored in the Keychain:", item)
    else:
        ui.warn("empty value, skipped")
        ui.hint("store it later with:", f"brain secret set {item}")


def add_preset(project: Path) -> str | None:
    available = presets.names()
    labels = [f"{presets.PRESETS[n].title}  —  {presets.PRESETS[n].description}" for n in available]
    labels.append("none of these — go back")
    picked = _choose("Which system?", labels, default=0)
    if picked >= len(available):
        return None
    preset = presets.PRESETS[available[picked]]

    name = project_mod.slug(_ask("Name this source", default=preset.name))
    if not name:
        return None

    # Ask for the account-identifying constants up front: a preset installed
    # without them is a connector that exists and cannot run.
    variables: dict[str, str] = {}
    for var in preset.variables:
        if var.where:
            _why(f"{var.name}: {var.where}")
        answer = _ask(var.prompt + (f" (e.g. {var.example})" if var.example else ""))
        if answer:
            variables[var.name] = answer

    interval = _ask_interval(60)
    script = project_mod.create_connector(
        project, name, interval_minutes=interval, preset=preset.name, variables=variables
    )
    if script is None:
        return None

    if preset.needs_secret:
        _store_secret(project, name, preset.secret_prompt)
    for note in preset.notes:
        ui.info(note)
    # Step 4 of the flow runs this for you; the command is here for anyone who
    # would rather do it from a shell. sys.executable, not the script's shebang
    # — see cli._probe_command.
    ui.hint("see what the credential can actually read:",
            f"{sys.executable} {script} --out /tmp/probe --probe")
    return name


def add_api(project: Path) -> str | None:
    base = _ask("Base URL of the API (e.g. https://api.example.com)")
    if not base:
        return None
    name = project_mod.slug(_ask("Name this source (e.g. hubspot, notion, billing-api)"))
    if not name:
        return None
    interval = _ask_interval(60)
    script = project_mod.create_connector(project, name, interval_minutes=interval, api_base=base)
    if script is None:
        return None

    _store_secret(project, name, "API token")
    ui.warn(f"'{name}' needs the endpoints written: one collect_* function per object")
    ui.hint("edit:", str(script))
    return name


def add_custom(project: Path) -> str | None:
    name = project_mod.slug(_ask("Name this source (e.g. hubspot, notion, billing-api)"))
    if not name:
        return None
    interval = _ask_interval(60)
    script = project_mod.create_connector(project, name, interval_minutes=interval)
    if script is None:
        return None

    if _confirm("Does it need a credential (API key, token)?", default=True):
        _store_secret(project, name, "credential")
        ui.info("read it in the connector with: "
                f"get_secret({project_mod.secret_item_name(project, name)!r})")

    ui.warn(f"'{name}' needs code: fill in fetch_records() in")
    ui.hint("edit:", str(script))
    return name


