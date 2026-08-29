"""Connector orchestration: read a project's registry.yaml, run due connector
scripts, rebuild the graphify graph if anything changed.

No notion of "connector types" here — every connector is just an executable
script (see connector_template.py for the contract). That keeps this module
source-agnostic: supporting a new kind of system means writing a new
sync.py in the project, not extending this orchestrator.
"""
from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from brainiphy_cli import ui


@dataclass
class SyncReport:
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    graph_rebuilt: bool = False


# Distinguishes "graphify has nothing to do" from "graphify cannot run" — the
# second needs an actionable message, not a bare non-zero exit.
_NO_KEY_MARKERS = ("no LLM API key", "requires ANTHROPIC_API_KEY", "API key")

# graphify's `claude-cli` backend shells out to the locally-installed `claude`
# binary (`claude -p --output-format json`), so the semantic pass authenticates
# with the user's Claude Code Pro/Max subscription and needs no API key at all.
CLAUDE_CLI_BACKEND = "claude-cli"

# Every environment variable graphify's own detect_backend() looks at. It never
# returns claude-cli — that backend is deliberately excluded there, because it
# runs another program rather than reading a key — so choosing it is our job.
# The list only gates the automatic choice: if any of these is set, the user
# already configured a backend and we leave the decision to graphify.
_BACKEND_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "MOONSHOT_API_KEY",
    "DEEPSEEK_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "OLLAMA_BASE_URL",
    "OLLAMA_HOST",
)


def resolve_backend(explicit: str | None = None) -> str | None:
    """Which `--backend` to hand `graphify extract`, or None to let graphify pick.

    An explicit choice always wins. Otherwise: a configured API key stays in
    charge (graphify's detection is richer than ours — custom providers, Azure
    endpoints), and only when *nothing* is configured do we fall back to the
    Claude Code subscription. That fallback is the whole point: a first sync on
    a machine with Claude Code installed should index documents, not stop at
    "no LLM API key found".
    """
    if explicit:
        return explicit
    if any(os.environ.get(var) for var in _BACKEND_ENV_VARS):
        return None
    if shutil.which("claude"):
        return CLAUDE_CLI_BACKEND
    return None


def find_graphify() -> str:
    """Locate the graphify CLI: PATH first, then this interpreter's --user
    bin dir (matches wherever `pip install --user graphifyy` put it)."""
    on_path = shutil.which("graphify")
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / "graphify"
    if candidate.exists():
        return str(candidate)
    # Imported here, not at module scope: project.py imports this module, and
    # the cycle would break both. Same reason type_label() is deferred in run().
    from brainiphy_cli import project as project_mod

    raise FileNotFoundError(
        "graphify CLI not found on PATH or in the user site bin dir "
        f"({Path(site.getuserbase()) / 'bin'}). "
        f"Install it with: {project_mod.graphify_install_command()}"
    )


def load_registry(project: Path) -> list[dict]:
    registry_path = project / "connectors" / "registry.yaml"
    if not registry_path.exists():
        return []
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return data.get("connectors") or []


def _state_path(project: Path, name: str) -> Path:
    return project / "connectors" / "state" / f"{name}.json"


def is_due(project: Path, name: str, interval_minutes: float) -> bool:
    state_file = _state_path(project, name)
    if not state_file.exists():
        return True
    try:
        last_run = datetime.fromisoformat(json.loads(state_file.read_text())["last_run"])
        elapsed_minutes = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, OSError):
        # An unreadable state file means "run it": a corrupt timestamp must
        # cost one redundant run, never freeze a connector forever. TypeError
        # is in here because a naive timestamp — hand-edited, or written by a
        # build that predates the timezone — cannot be subtracted from an aware
        # one, and that exception used to escape and take down the whole sync
        # before any connector had run.
        return True
    return elapsed_minutes >= interval_minutes


def _mark_ran(project: Path, name: str) -> None:
    state_file = _state_path(project, name)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_run": datetime.now(timezone.utc).isoformat()}))


def build_graph(project: Path, *, full: bool = False, backend: str | None = None) -> tuple[bool, str]:
    """Rebuild the graph. Returns (succeeded, what-was-run).

    Two different graphify commands, and picking the wrong one silently does
    nothing:
      - `graphify extract` is the full pass. It is the only one that indexes
        documents (Markdown, PDFs, …), which is what a business brain is made
        of, and it needs an LLM backend to do it.
      - `graphify update` only re-extracts *code* files with a local AST pass,
        no API key. Cheap, but a no-op on a corpus of documents.

    So: full pass the first time (and whenever asked), incremental afterwards.

    Only the full pass takes a backend — `update` is a local AST pass with no
    model in it, and passing --backend to it is not just useless but rejected.

    --no-gitignore is not optional here. graphify honors .gitignore, and
    `brain init` puts raw/ in it (mirrored content should not be committed) —
    without this flag graphify skips the entire corpus and reports finding
    nothing. .graphifyignore still applies, which is what keeps connectors/
    out of the index.
    """
    graphify = find_graphify()
    graph_json = project / "graphify-out" / "graph.json"
    first_build = not graph_json.exists()

    if full or first_build:
        cmd = [graphify, "extract", str(project), "--no-gitignore"]
        chosen = resolve_backend(backend)
        if chosen:
            cmd += ["--backend", chosen]
        if chosen == CLAUDE_CLI_BACKEND:
            # Worth saying out loud: the run is about to spend plan usage rather
            # than API credit, and it runs one chunk at a time (graphify forces
            # concurrency 1 for this backend), so a big corpus takes a while.
            ui.info("indexing through your Claude Code subscription (no API key needed)")
    else:
        cmd = [graphify, "update", str(project)]
    # Short label for the report table — the full command line would wrap it.
    label = f"graphify {cmd[1]}"

    with ui.working(f"rebuilding graph: {label} {ui.short_path(project)}"):
        result = subprocess.run(cmd, capture_output=True, text=True)
    ui.raw(result.stdout)

    if result.returncode == 0 and graph_json.exists():
        return True, label

    combined = result.stdout + result.stderr
    ui.raw(result.stderr, stderr=True)

    if any(marker in combined for marker in _NO_KEY_MARKERS):
        # Not a brainiphy failure: indexing documents needs a model. Spell out
        # every way out — the two that cost nothing extra come first, because a
        # user without an API key is exactly who this message reaches.
        ui.error("graphify needs an LLM backend to index documents")
        ui.hint("use your Claude Code subscription (install Claude Code, then):", f"brain sync --backend {CLAUDE_CLI_BACKEND}")
        ui.hint("or let Claude Code do the extraction — open the project and run:", "/graphify")
        ui.hint("or export a key first, e.g.:", "export ANTHROPIC_API_KEY=…   # or GEMINI_API_KEY, OPENAI_API_KEY…")
        return False, label

    ui.error(f"{label} failed")
    return False, label


def run(project: Path, *, dry_run: bool = False, full: bool = False, backend: str | None = None) -> SyncReport:
    project = project.resolve()
    connectors = load_registry(project)
    report = SyncReport()

    if not connectors:
        ui.warn("0 connectors registered in", project / "connectors/registry.yaml")
        # A brain can still have content without connectors (`graphify add
        # <url>` writes straight into raw/), so a --full run keeps going and
        # rebuilds; a plain one has nothing to do.
        if not full:
            return report

    any_ran = False
    if dry_run:
        # Deferred: project.py imports this module, so the type label — pure
        # display metadata, unused by the run itself — comes in here rather
        # than at the top.
        from brainiphy_cli import project as project_mod

        dry_table = ui.table("connector", "type", "interval", "state", "script")
    else:
        dry_table = None

    for entry in connectors:
        # registry.yaml is a file people edit by hand. A malformed entry costs
        # that connector and is reported; it does not abort the ones after it.
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name:
            ui.error("registry entry with no name, skipped:", entry)
            report.errors.append(f"registry entry without a name: {entry!r}")
            continue
        try:
            interval = float(entry.get("interval_minutes", 60))
        except (TypeError, ValueError):
            ui.warn(f"{name}: unreadable interval_minutes, using 60")
            interval = 60
        script = project / "connectors" / name / "sync.py"
        due = is_due(project, name, interval)

        if dry_run:
            dry_table.add_row(
                ui.cell(name, "brain.path"),
                ui.cell(project_mod.type_label(entry, project)),
                ui.cell(f"{interval:g} min"),
                ui.cell("would run", "brain.warn") if due else ui.cell("not due", "brain.info"),
                ui.cell("ok", "brain.ok") if script.exists() else ui.cell("MISSING sync.py", "brain.err"),
            )
            continue

        if not due:
            report.skipped.append(name)
            continue
        if not script.exists():
            ui.error(f"{name}: skipped, no script at", script)
            report.errors.append(f"{name}: missing {script}")
            continue

        out_dir = project / "raw" / name
        out_dir.mkdir(parents=True, exist_ok=True)
        with ui.working(f"running {name} -> {out_dir}"):
            result = subprocess.run(
                [sys.executable, str(script), "--out", str(out_dir)],
                capture_output=True,
                text=True,
            )
        if result.returncode != 0:
            ui.error(f"{name}: failed (exit {result.returncode})")
            ui.raw(result.stdout)
            ui.raw(result.stderr, stderr=True)
            report.errors.append(f"{name}: exit {result.returncode}")
            continue

        ui.ok(f"{name} ->", out_dir)
        ui.raw(result.stdout)
        _mark_ran(project, name)
        report.ran.append(name)
        any_ran = True

    if dry_run:
        ui.print_table(dry_table)
        return report

    # A --full run rebuilds even when no connector was due: the corpus may have
    # changed underneath (a mirrored folder edited by hand, `graphify add`).
    if any_ran or full:
        succeeded, label = build_graph(project, full=full, backend=backend)
        if succeeded:
            ui.ok("graph rebuilt")
            # graphify signs off by suggesting its own `cluster-only`, which is
            # a dead end from here. The picture it would draw is what `brain
            # view` draws, on demand and without a model.
            ui.hint("see it as a picture:", f"brain view {ui.short_path(project)}")
            report.graph_rebuilt = True
        else:
            report.errors.append(f"{label} failed")
    elif not report.errors:
        ui.info("nothing due, graph left as-is")

    return report
