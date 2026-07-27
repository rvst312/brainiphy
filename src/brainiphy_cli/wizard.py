"""`brain new` — the guided end-to-end flow for building a brain.

Everything this does is also available as an individual command; the wizard
adds the part that used to live only in a human's (or an agent's) head: the
order of the steps, what each one is for, and which of them a given project
still needs. It walks the same seven steps `brain guide` reports on, calling
the same functions in project.py, and prints the checklist on the way out so
whatever it could not do for you is spelled out.

Re-running it on an existing brain is safe and expected: each step detects
what is already there and offers to skip it.
"""
from __future__ import annotations

import getpass
import subprocess
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

TOTAL_STEPS = 7

# What a source can be, in the order SKILL.md recommends trying: cheapest and
# most automatic first, bespoke code last.
SOURCE_CHOICES = [
    "a system brainiphy already knows (ready-made connector — just answer a couple of questions)",
    "a folder on this Mac        (mirrored automatically — no code to write)",
    "a public URL                (fetched by graphify directly)",
    "a REST API                  (plumbing done, you write the endpoints)",
    "something else              (bare connector for you to fill in)",
    "nothing more — move on",
]
DONE_CHOICE = len(SOURCE_CHOICES) - 1


class Cancelled(Exception):
    """The user hit Ctrl-C / Ctrl-D at a prompt. Unwinds to run()."""


def _step(number: int, title: str) -> None:
    ui.blank()
    line = Text()
    line.append(f"Step {number}/{TOTAL_STEPS}", style="brain.hint")
    line.append("  ")
    line.append(title, style="brain.head")
    ui.out.print(line)
    ui.out.rule(style="brain.info")


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


# ------------------------------------------------------------------ steps --

def _step_location(project_arg: str | None) -> Path:
    _step(1, "Where should the brain live?")
    _why("A brain is a folder: your sources get mirrored into it and the graph is built there.")
    if project_arg is not None:
        chosen = Path(project_arg).expanduser().resolve()
        chosen.mkdir(parents=True, exist_ok=True)
        ui.ok("using", chosen)
        return chosen
    chosen = picker.pick_project_dir()
    if chosen is None:
        raise Cancelled
    return chosen


def _step_graphify() -> None:
    _step(2, "Check graphify")
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
        ui.error("install failed — do it by hand and re-run `brain new`")
        return
    ui.ok("graphify installed")


def _step_scaffold(project: Path) -> None:
    _step(3, "Prepare the folder")
    _why("Creates connectors/registry.yaml and the ignore files. Safe on an existing project.")
    project_mod.scaffold(project)


def add_local_folder(project: Path) -> str | None:
    source = _ask_path("Which folder? (paste the path)")
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
    url = _ask("Which URL?")
    if not url:
        return None
    try:
        graphify = project_mod.find_exe("graphify")
    except FileNotFoundError:
        ui.error("graphify is not installed, so it cannot fetch that URL")
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
    ui.hint("see what the credential can actually read:", f"{script} --out /tmp/probe --probe")
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


def _step_sources(project: Path) -> list[str]:
    _step(4, "What feeds this brain?")
    _why("Each source becomes a connector. Add as many as you like — you can always add more later.")

    existing = project_mod.load_registry_entries(project)
    if existing:
        ui.info("already registered: " + ", ".join(e.get("name", "?") for e in existing))

    added: list[str] = []
    handlers = (add_preset, add_local_folder, add_url, add_api, add_custom)
    while True:
        choice = _choose(
            "Add a source:", SOURCE_CHOICES, default=DONE_CHOICE if (existing or added) else 0
        )
        if choice == DONE_CHOICE:
            break
        name = handlers[choice](project)
        if name:
            added.append(name)

    if not added and not existing:
        ui.warn("no sources yet — the brain will be empty until you add one")
    return added


def _step_build(project: Path) -> None:
    _step(5, "First build")
    _why("Pulls every source in and indexes it. Documents need an LLM backend — an API key,")
    _why("or Claude Code itself running /graphify in the folder. brain sync will say which.")

    pending = steps.unimplemented_connectors(project)
    if pending:
        ui.warn("these still have no fetch_records() and will fail this run: " + ", ".join(pending))
        ui.info("that is expected — implement them, then run brain sync again")

    if not _confirm("Run it now?", default=True):
        ui.hint("run it later with:", f"brain sync {ui.short_path(project)} --full")
        return

    try:
        # full=True: first build, so index documents too, not just code — and
        # rebuild even if nothing was due (a URL-only brain has no connectors).
        sync_mod.run(project, full=True)
    except FileNotFoundError as exc:
        ui.error(str(exc))


def _step_claude(project: Path) -> None:
    _step(6, "Connect it to Claude")
    _why("Claude Code reads the graph through CLAUDE.md + hooks. Claude Desktop needs an MCP server.")

    if not _confirm("Wire this brain into Claude Code?", default=True):
        ui.hint("do it later with:", f"brain connect-claude {ui.short_path(project)}")
        return

    desktop = _confirm("Also register it in Claude Desktop (MCP server)?", default=False)
    trust = False
    if desktop:
        trust = _confirm("Add the folder to Desktop's trusted folders? (appends, never replaces)", default=False)
    project_mod.connect_claude(project, desktop=desktop, trust_desktop=trust)


def _step_schedule(project: Path) -> None:
    _step(7, "Keep it in sync")
    _why("A LaunchAgent re-runs `brain sync` in the background so the graph does not go stale.")

    if not project_mod.load_registry_entries(project):
        ui.info("no connectors registered, so there is nothing to schedule yet")
        ui.hint("once you add one:", f"brain schedule {ui.short_path(project)} --interval-minutes 15 --load")
        return

    if not _confirm("Schedule automatic syncing?", default=True):
        ui.hint("do it later with:", f"brain schedule {ui.short_path(project)} --interval-minutes 15 --load")
        return

    interval = _ask_interval(15)
    project_mod.schedule(project, interval_minutes=interval, load=True)


# ------------------------------------------------------------------- run ---

def run(project_arg: str | None) -> int:
    if not picker.is_interactive():
        ui.error("`brain new` needs a terminal — it asks questions")
        ui.hint("in a script, use the individual commands instead:", "brain init … && brain new-connector … && brain sync …")
        return 1

    ui.header("brain new", "guided setup — 7 steps, everything is optional")

    project: Path | None = None
    try:
        project = _step_location(project_arg)
        _step_graphify()
        _step_scaffold(project)
        _step_sources(project)
        _step_build(project)
        _step_claude(project)
        _step_schedule(project)
    except Cancelled:
        ui.blank()
        ui.warn("cancelled — nothing after this point was changed")
        if project is not None:
            ui.hint("pick up where you left off with:", f"brain guide {ui.short_path(project)}")
        return 1

    ui.blank()
    ui.header("Where this brain stands", project)
    steps.render(steps.inspect(project))
    return 0
