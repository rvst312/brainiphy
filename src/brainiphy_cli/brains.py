"""The list of brains this machine knows about.

Until now brainiphy had no idea a brain existed unless you were standing in it
or typed its path. That is fine for one; it stops being fine the moment you
keep one per client, because the tool cannot answer the first question anybody
asks — "which brains do I have, and which one has gone stale?"

This module is that list, and deliberately little else:

  - It stores **paths and nothing more**. Every fact shown about a brain (how
    far along it is, how big its graph is, when it last synced) is read from
    the brain itself at display time. A cache would go stale exactly when it
    matters, and there would then be two answers to "is this brain built".
  - Forgetting a brain removes the entry, never the folder. `brain forget` is
    a navigation command, not a destructive one; deleting a client's knowledge
    base is something a person should have to do with `rm`, on purpose.
  - A registered folder that has been moved or deleted is reported as missing
    rather than silently dropped — a brain vanishing from the list on its own
    is indistinguishable from a bug.

The file lives at ~/.config/brainiphy/brains.yaml, or under $BRAINIPHY_HOME
when that is set (which is also what keeps the tests off the real one).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

HEADER = """\
# Brains brainiphy knows about, newest last. Managed by `brain add`/`brain
# forget`; editing it by hand is fine, it is only a list of paths.
#
# Removing an entry here never touches the brain's own files.
"""


def home() -> Path:
    """Where brainiphy keeps its own state, as opposed to a brain's."""
    override = os.environ.get("BRAINIPHY_HOME")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "brainiphy"


def registry_path() -> Path:
    return home() / "brains.yaml"


def is_brain(path: Path) -> bool:
    """A folder is a brain once it has been scaffolded."""
    return (path / "connectors" / "registry.yaml").exists()


# ------------------------------------------------------------- the list ----

def known() -> list[Path]:
    """Registered paths, in the order they were added."""
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        # A corrupt list must not take down every command that shows it. The
        # entries are recoverable by hand; the tool staying usable is not.
        return []
    out: list[Path] = []
    for entry in data.get("brains") or []:
        raw = entry.get("path") if isinstance(entry, dict) else entry
        if not raw:
            continue
        resolved = Path(str(raw)).expanduser()
        if resolved not in out:
            out.append(resolved)
    return out


def _write(paths: list[Path]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"brains": [{"path": str(p)} for p in paths]},
        sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")


def remember(project: Path) -> bool:
    """Add a brain to the list. Returns False if it was already there.

    Called wherever a brain comes into existence or is opened, so the list
    fills itself in as you work rather than having to be curated.
    """
    project = project.expanduser().resolve()
    paths = known()
    if project in paths:
        return False
    paths.append(project)
    _write(paths)
    return True


def forget(project: Path) -> bool:
    """Drop a brain from the list, leaving every one of its files alone."""
    project = project.expanduser().resolve()
    paths = known()
    remaining = [p for p in paths if p != project]
    if len(remaining) == len(paths):
        return False
    _write(remaining)
    return True


# ----------------------------------------------------------- what to show ----

@dataclass
class Summary:
    """Everything the list shows about one brain, read live from disk."""

    path: Path
    exists: bool = True
    scaffolded: bool = True
    steps_done: int = 0
    steps_total: int = 7
    sources: int = 0
    sources_ready: int = 0
    nodes: int | None = None
    last_sync: datetime | None = None
    visualizer: Path | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def missing(self) -> bool:
        """Registered, but the folder is gone or is no longer a brain."""
        return not (self.exists and self.scaffolded)

    @property
    def complete(self) -> bool:
        return self.steps_done >= self.steps_total

    @property
    def pending_sources(self) -> int:
        return max(0, self.sources - self.sources_ready)


def _last_sync(project: Path) -> datetime | None:
    """When any connector last ran. The graph's own mtime is the fallback, so
    a brain fed by `graphify add` rather than by connectors still shows one."""
    latest: datetime | None = None
    state_dir = project / "connectors" / "state"
    if state_dir.is_dir():
        for state_file in state_dir.glob("*.json"):
            try:
                import json

                stamp = datetime.fromisoformat(
                    json.loads(state_file.read_text())["last_run"])
            except Exception:  # noqa: BLE001 — a bad state file is not worth a crash here
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            if latest is None or stamp > latest:
                latest = stamp
    if latest is None:
        graph = project / "graphify-out" / "graph.json"
        if graph.exists():
            latest = datetime.fromtimestamp(graph.stat().st_mtime, timezone.utc)
    return latest


def graph_html(project: Path) -> Path | None:
    """The visualizer graphify writes, if it has been written at all."""
    candidate = project / "graphify-out" / "graph.html"
    return candidate if candidate.exists() else None


def visualizer_is_stale(project: Path) -> bool:
    """True when graph.html is older than the graph it draws.

    `graphify extract` writes graph.json and does not regenerate graph.html —
    only `update` and `cluster-only` do — so after a full rebuild the picture
    on screen is quietly the previous one.
    """
    html = project / "graphify-out" / "graph.html"
    graph = project / "graphify-out" / "graph.json"
    if not graph.exists():
        return False
    if not html.exists():
        return True
    return html.stat().st_mtime < graph.stat().st_mtime


def summarize(project: Path) -> Summary:
    """Read one brain's current state. Cheap enough to do for every row."""
    from brainiphy_cli import steps as steps_mod

    project = project.expanduser()
    if not project.is_dir():
        return Summary(path=project, exists=False, scaffolded=False)
    if not is_brain(project):
        return Summary(path=project, exists=True, scaffolded=False)

    state = steps_mod.inspect(project)
    entries = steps_mod._registry_entries(project)
    pending = {name for name, _ in steps_mod._pending_connectors(project)}
    graph = steps_mod._graph_size(project)

    return Summary(
        path=project,
        steps_done=state.done_count,
        steps_total=len(state.steps),
        sources=len(entries),
        sources_ready=sum(1 for e in entries if e.get("name") not in pending),
        nodes=graph[0] if graph else None,
        last_sync=_last_sync(project),
        visualizer=graph_html(project),
    )


def summaries() -> list[Summary]:
    return [summarize(p) for p in known()]


def ago(moment: datetime | None) -> str:
    """A relative time short enough for a table column."""
    if moment is None:
        return "never"
    seconds = (datetime.now(timezone.utc) - moment).total_seconds()
    if seconds < 90:
        return "just now"
    for size, unit in ((3600, "m"), (86400, "h"), (86400 * 7, "d")):
        if seconds < size:
            divisor = {"m": 60, "h": 3600, "d": 86400}[unit]
            return f"{int(seconds // divisor)}{unit} ago"
    return f"{int(seconds // 86400)}d ago"
