"""What building a brain actually consists of, and how far along a project is.

This module is the single definition of the playbook (the same one SKILL.md
describes in prose): seven ordered steps, each able to look at a project on
disk and say whether it is already done. `brain guide` renders it, `brain
status` uses it for its next-step line, and `brain new` walks it. Adding or
reordering a step means editing `inspect()` here and nothing else.

`render()` lives here too rather than in cli.py — the wizard prints the same
checklist on its way out, and cli.py importing wizard.py importing cli.py
would be a cycle.

Detection is deliberately read-only and cheap — it runs on every `brain
status`, so it inspects file existence and small JSON/YAML files, never
subprocesses (beyond resolving an executable on PATH).
"""
from __future__ import annotations

import json
import re
import shutil
import site
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rich.text import Text

from brainiphy_cli import ui

DONE = "done"
TODO = "todo"
# Nothing to do here for this particular brain — e.g. a brain fed only by
# mirrored folders has no connector to implement. Not pending (it must not
# hold up the "what's next" pointer) but not an achievement either, so it
# renders as a dash instead of a checkmark.
SKIP = "skip"

# Marker left by connector_template.py in a connector that has not been
# implemented yet. Checked as source text rather than by importing the script:
# a half-written connector may not even import cleanly.
UNIMPLEMENTED_MARKER = "raise NotImplementedError"


@dataclass
class Step:
    number: int
    key: str
    title: str
    why: str
    state: str = TODO
    detail: str = ""
    command: str | None = None
    # Extra copy-pasteable commands shown under the step (e.g. one $EDITOR line
    # per connector still missing its fetch_records).
    extra_commands: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        """Settled — either really done or not applicable. Drives "what's next"."""
        return self.state in (DONE, SKIP)


@dataclass
class BrainState:
    project: Path
    steps: list[Step]

    @property
    def next_step(self) -> Step | None:
        """First step still pending — what the user should do right now."""
        return next((s for s in self.steps if not s.done), None)

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.done)

    @property
    def complete(self) -> bool:
        return self.next_step is None


def _find_exe(name: str) -> str | None:
    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / name
    return str(candidate) if candidate.exists() else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def _registry_entries(project: Path) -> list[dict]:
    path = project / "connectors" / "registry.yaml"
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    return data.get("connectors") or []


def _graph_size(project: Path) -> tuple[int, int] | None:
    """(nodes, edges) if a parseable graph exists, else None."""
    graph_json = project / "graphify-out" / "graph.json"
    if not graph_json.exists():
        return None
    try:
        data = json.loads(graph_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return len(data.get("nodes", [])), len(data.get("links", data.get("edges", [])))


def _claude_code_wired(project: Path) -> bool:
    """graphify claude install leaves two traces; either one counts, since a
    user may have trimmed CLAUDE.md by hand."""
    claude_md = project / "CLAUDE.md"
    if claude_md.exists() and "graphify" in claude_md.read_text(encoding="utf-8", errors="ignore"):
        return True
    settings = project / ".claude" / "settings.json"
    if settings.exists() and "graphify" in settings.read_text(encoding="utf-8", errors="ignore"):
        return True
    return False


def _launch_agent(project: Path) -> Path | None:
    plist = Path.home() / "Library/LaunchAgents" / f"com.graphify.sync.{_slug(project.name)}.plist"
    return plist if plist.exists() else None


def unimplemented_connectors(project: Path) -> list[str]:
    """Registered connectors whose sync.py is missing or still the untouched
    template."""
    pending = []
    for entry in _registry_entries(project):
        name = entry.get("name")
        if not name:
            continue
        script = project / "connectors" / name / "sync.py"
        if not script.exists():
            pending.append(name)
            continue
        if UNIMPLEMENTED_MARKER in script.read_text(encoding="utf-8", errors="ignore"):
            pending.append(name)
    return pending


def inspect(project: Path) -> BrainState:
    project = project.resolve()
    short = ui.short_path(project)
    entries = _registry_entries(project)
    graph = _graph_size(project)

    steps: list[Step] = []

    # 1 -------------------------------------------------------------------
    graphify = _find_exe("graphify")
    steps.append(
        Step(
            1,
            "graphify",
            "Install graphify",
            "the engine that turns the collected files into a graph",
            state=DONE if graphify else TODO,
            detail=graphify or "not found on PATH",
            command=None if graphify else "pip3 install --user graphifyy",
        )
    )

    # 2 -------------------------------------------------------------------
    registry_exists = (project / "connectors" / "registry.yaml").exists()
    ignore_path = project / ".graphifyignore"
    ignore_ok = ignore_path.exists() and "connectors/" in ignore_path.read_text(encoding="utf-8", errors="ignore")
    scaffolded = registry_exists and ignore_ok
    steps.append(
        Step(
            2,
            "scaffold",
            "Scaffold the project",
            "registry.yaml, .gitignore and .graphifyignore, so connector scripts are not indexed as content",
            state=DONE if scaffolded else TODO,
            detail=(
                "connectors/registry.yaml + .graphifyignore"
                if scaffolded
                else ("registry.yaml exists but .graphifyignore is missing connectors/" if registry_exists else "not scaffolded yet")
            ),
            command=None if scaffolded else f"brain init {short}",
        )
    )

    # 3 -------------------------------------------------------------------
    # A brain fed only by `graphify add <url>` has no connectors at all, so a
    # non-empty graph counts as having sources just as much as a registry does.
    has_sources = bool(entries) or bool(graph and graph[0])
    steps.append(
        Step(
            3,
            "sources",
            "Add data sources",
            "one connector per system that feeds the brain: a folder, a URL, a CRM",
            state=DONE if has_sources else TODO,
            detail=(
                f"{len(entries)} connector(s): " + ", ".join(e.get("name", "?") for e in entries)
                if entries
                else ("no connectors, but the graph already has content" if has_sources else "no sources yet")
            ),
            command=None if has_sources else f"brain new {short}",
        )
    )

    # 4 -------------------------------------------------------------------
    pending = unimplemented_connectors(project)
    steps.append(
        Step(
            4,
            "implement",
            "Implement the custom connectors",
            "fill in fetch_records() for anything brain could not generate on its own",
            state=TODO if pending else (DONE if entries else SKIP),
            detail=(
                "still template-only: " + ", ".join(pending)
                if pending
                else (
                    "every registered connector is implemented"
                    if entries
                    else "no connectors that need code"
                )
            ),
            command=None,
            extra_commands=[f"$EDITOR {short}/connectors/{name}/sync.py" for name in pending],
        )
    )

    # 5 -------------------------------------------------------------------
    steps.append(
        Step(
            5,
            "build",
            "Run the first sync",
            "pull every source in and index it — documents need an LLM key, or /graphify inside Claude Code",
            state=DONE if graph else TODO,
            detail=f"{graph[0]} nodes, {graph[1]} edges" if graph else "graph not built yet",
            command=None if graph else f"brain sync {short} --full",
        )
    )

    # 6 -------------------------------------------------------------------
    wired = _claude_code_wired(project)
    steps.append(
        Step(
            6,
            "claude",
            "Connect it to Claude",
            "so Claude Code (and optionally Desktop) can query the graph",
            state=DONE if wired else TODO,
            detail="CLAUDE.md + hooks in place" if wired else "not connected yet",
            command=None if wired else f"brain connect-claude {short} --desktop --trust-desktop",
        )
    )

    # 7 -------------------------------------------------------------------
    plist = _launch_agent(project)
    steps.append(
        Step(
            7,
            "schedule",
            "Keep it in sync",
            "a LaunchAgent that re-runs brain sync on its own",
            state=DONE if plist else TODO,
            detail=str(plist) if plist else "not scheduled — sync is manual for now",
            command=None if plist else f"brain schedule {short} --interval-minutes 15 --load",
        )
    )

    return BrainState(project=project, steps=steps)


# ------------------------------------------------------------- rendering ----

def render(state: BrainState, *, verbose: bool = False) -> None:
    """The checklist `brain guide` prints (and `brain new` prints on the way
    out). Lives here, next to the step definitions, so both callers stay in
    sync — and so cli.py and wizard.py don't have to import each other.
    """
    total = len(state.steps)
    next_step = state.next_step

    summary = Text()
    summary.append(f"{state.done_count}/{total} done", style="brain.ok" if state.complete else "brain.head")
    summary.append("   ✓ done  ▸ next  ○ pending  – n/a", style="brain.info")
    ui.out.print(summary)
    ui.blank()

    for step in state.steps:
        is_next = next_step is not None and step.key == next_step.key
        icon, icon_style, title_style = (
            ("–", "brain.info", "brain.info")
            if step.state == SKIP
            else ("✓", "brain.ok", "brain.info")
            if step.done
            else ("▸", "brain.warn", "brain.head")
            if is_next
            else ("○", "brain.info", "brain.info")
        )

        line = Text(" ")
        line.append(icon, style=icon_style)
        line.append(f" {step.number}  ", style="brain.info")
        line.append(step.title, style=title_style)
        ui.out.print(line, soft_wrap=True)

        # Detail on every unfinished step (it says what is missing) and on n/a
        # ones (it says why they don't apply), but on completed ones only when
        # asked — otherwise a finished brain prints a wall of text.
        if step.detail and (not step.done or step.state == SKIP or verbose):
            ui.out.print(Text("       " + step.detail, style="brain.info"), soft_wrap=True)
        if step.why and is_next:
            ui.out.print(Text("       " + step.why, style="brain.info"), soft_wrap=True)

        if not step.done:
            for command in ([step.command] if step.command else []) + step.extra_commands:
                ui.out.print(Text("       " + command, style="bold" if is_next else "brain.info"), soft_wrap=True)

    ui.blank()
    if state.complete:
        ui.ok("this brain is fully set up")
        ui.hint("keep it fresh by hand any time with:", f"brain sync {ui.short_path(state.project)}")
    elif next_step is not None:
        ui.hint("next step:", next_step.command or (next_step.extra_commands[0] if next_step.extra_commands else ""))
