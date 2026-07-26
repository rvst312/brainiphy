"""Connector orchestration: read a project's registry.yaml, run due connector
scripts, rebuild the graphify graph if anything changed.

No notion of "connector types" here — every connector is just an executable
script (see connector_template.py for the contract). That keeps this module
source-agnostic: supporting a new kind of system means writing a new
sync.py in the project, not extending this orchestrator.
"""
from __future__ import annotations

import json
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


def find_graphify() -> str:
    """Locate the graphify CLI: PATH first, then this interpreter's --user
    bin dir (matches wherever `pip install --user graphifyy` put it)."""
    on_path = shutil.which("graphify")
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / "graphify"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(
        "graphify CLI not found on PATH or in the user site bin dir "
        f"({Path(site.getuserbase()) / 'bin'}). Install it with: pip3 install --user graphifyy"
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
    except (json.JSONDecodeError, KeyError, ValueError):
        return True
    elapsed_minutes = (datetime.now(timezone.utc) - last_run).total_seconds() / 60
    return elapsed_minutes >= interval_minutes


def _mark_ran(project: Path, name: str) -> None:
    state_file = _state_path(project, name)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"last_run": datetime.now(timezone.utc).isoformat()}))


def run(project: Path, *, dry_run: bool = False) -> SyncReport:
    project = project.resolve()
    connectors = load_registry(project)
    report = SyncReport()

    if not connectors:
        ui.warn("0 connectors registered in", project / "connectors/registry.yaml")
        return report

    any_ran = False
    dry_table = ui.table("connector", "interval", "state", "script") if dry_run else None

    for entry in connectors:
        name = entry["name"]
        interval = float(entry.get("interval_minutes", 60))
        script = project / "connectors" / name / "sync.py"
        due = is_due(project, name, interval)

        if dry_run:
            dry_table.add_row(
                ui.cell(name, "brain.path"),
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

    if any_ran:
        graphify = find_graphify()
        with ui.working(f"rebuilding graph: graphify update {project}"):
            result = subprocess.run([graphify, "update", str(project)], capture_output=True, text=True)
        ui.raw(result.stdout)
        if result.returncode != 0:
            ui.raw(result.stderr, stderr=True)
            ui.error("graphify update failed")
            report.errors.append("graphify update failed")
        else:
            ui.ok("graph rebuilt")
            report.graph_rebuilt = True
    elif not report.errors:
        ui.info("nothing due, graph left as-is")

    return report
