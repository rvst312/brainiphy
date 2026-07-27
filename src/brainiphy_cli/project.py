"""Operations on a target project: scaffolding, connector creation, wiring it
into Claude, and installing the sync schedule.

Extracted out of cli.py so the individual commands (`brain init`,
`brain new-connector`, `brain connect-claude`, `brain schedule`) and the guided
flow (`brain new`) run the *same* code instead of drifting apart — a wizard
that reimplements scaffolding is a wizard that eventually scaffolds something
subtly different.

Everything here prints its own progress through ui and returns a plain value;
argparse plumbing and exit codes stay in cli.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import site
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from brainiphy_cli import sync as sync_mod, ui

TEMPLATE_DIR = Path(__file__).resolve().parent

REGISTRY_HEADER = """\
# Connector registry for this brain. Format consumed by `brain sync`.
#
# Every entry requires a script at connectors/<name>/sync.py — use
# `brain new-connector <project> <name>` to create one from the template.
"""

GITIGNORE_ENTRIES = [
    "connectors/state/",
    "connectors/logs/",
    "mirrors/",
    "raw/",
    "graphify-out/",
]

# connectors/ holds tooling (sync.py scripts, state, logs), not knowledge
# content — without this, graphify's AST extractor indexes the connector
# scripts themselves as source code (functions, imports) alongside the real
# data they produce in raw//mirrors/.
#
# Note the two ignore files are NOT interchangeable, in either direction:
# .graphifyignore is the only one graphify always obeys, but it does honor
# .gitignore as well unless --no-gitignore is passed. Since raw/ is in
# .gitignore above (mirrored content should not be committed), every graphify
# invocation from sync.build_graph passes --no-gitignore — otherwise the whole
# corpus is skipped and graphify reports an empty project.
GRAPHIFYIGNORE_ENTRIES = [
    "connectors/",
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def find_exe(name: str) -> str:
    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / name
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"{name} not found on PATH or in {Path(site.getuserbase()) / 'bin'}")


def append_ignore_entries(path: Path, entries: list[str], header_comment: str) -> int:
    """Append any missing lines to a gitignore-syntax file, creating it if
    needed. Returns how many lines were added."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in entries if line not in existing]
    if not missing:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(f"\n{header_comment}\n")
        for line in missing:
            fh.write(line + "\n")
    return len(missing)


def load_registry_entries(project: Path) -> list[dict]:
    # Same reader `brain sync` uses at run time — one parse behavior, not two.
    return sync_mod.load_registry(project)


def write_registry_entries(project: Path, entries: list[dict]) -> None:
    registry_path = project / "connectors" / "registry.yaml"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"connectors": entries}, sort_keys=False, default_flow_style=False)
    registry_path.write_text(REGISTRY_HEADER + "\n" + body, encoding="utf-8")


# ------------------------------------------------------------- scaffold ----

def scaffold(project: Path) -> None:
    """Create connectors/, registry.yaml and the two ignore files. Idempotent —
    safe to re-run on a project that is already half set up."""
    project.mkdir(parents=True, exist_ok=True)
    (project / "connectors" / "state").mkdir(parents=True, exist_ok=True)

    registry_path = project / "connectors" / "registry.yaml"
    if registry_path.exists():
        ui.info("registry.yaml already exists, leaving it alone")
    else:
        registry_path.write_text(REGISTRY_HEADER + "\nconnectors: []\n", encoding="utf-8")
        ui.ok("created", registry_path.relative_to(project))

    added = append_ignore_entries(
        project / ".gitignore", GITIGNORE_ENTRIES, "# brainiphy: generated output, do not version"
    )
    if added:
        ui.ok(f".gitignore: {added} new {'entry' if added == 1 else 'entries'}")
    else:
        ui.info(".gitignore already covers everything")

    added = append_ignore_entries(
        project / ".graphifyignore",
        GRAPHIFYIGNORE_ENTRIES,
        "# brainiphy: do not index connector scripts as source code",
    )
    if added:
        ui.ok(f".graphifyignore: {added} new {'entry' if added == 1 else 'entries'}")
    else:
        ui.info(".graphifyignore already covers everything")


# ---------------------------------------------------- connector creation ----

def create_connector(
    project: Path,
    name: str,
    *,
    interval_minutes: float = 60,
    mirror: Path | None = None,
) -> Path | None:
    """Write connectors/<name>/sync.py from the right template and register it.

    `mirror` picks the ready-to-run rsync template over the generic one that
    needs fetch_records() filled in. Returns the script path, or None when a
    script was already there (never overwritten — it may hold real work).
    """
    connector_dir = project / "connectors" / name
    script_path = connector_dir / "sync.py"

    if script_path.exists():
        ui.error("already exists, not overwriting it:", script_path)
        return None

    connector_dir.mkdir(parents=True, exist_ok=True)
    template_name = "mirror_template.py" if mirror else "connector_template.py"
    template = (TEMPLATE_DIR / template_name).read_text(encoding="utf-8")
    template = template.replace('SOURCE_SYSTEM = "REPLACE_ME"', f'SOURCE_SYSTEM = "{name}"')
    if mirror:
        # repr() so a folder name with a quote or a backslash in it still
        # produces a valid literal.
        template = template.replace(
            'MIRROR_SOURCE = Path("REPLACE_ME_SOURCE")', f"MIRROR_SOURCE = Path({str(mirror)!r})"
        )
    script_path.write_text(template, encoding="utf-8")
    script_path.chmod(0o755)
    ui.ok("created", script_path.relative_to(project))

    entries = load_registry_entries(project)
    if any(e.get("name") == name for e in entries):
        ui.info(f"{name} was already in registry.yaml, not duplicating it")
    else:
        entries.append({"name": name, "interval_minutes": interval_minutes})
        write_registry_entries(project, entries)
        ui.ok(f"registered {name} in registry.yaml, every {interval_minutes:g} min")

    return script_path


def secret_item_name(project: Path, connector: str) -> str:
    """Conventional Keychain item name for a connector's credential."""
    return f"graphify-{slug(project.name)}-{connector}"


# -------------------------------------------------------- connect to Claude --

DESKTOP_CONFIG = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"


def connect_claude(project: Path, *, desktop: bool = False, trust_desktop: bool = False) -> bool:
    """Wire the project into Claude Code, and optionally Claude Desktop.

    Returns False on failure. The Desktop config is always backed up before
    being written, and `localAgentModeTrustedFolders` is only ever appended to
    — replacing it would silently revoke folders the user trusts.
    """
    with ui.working("graphify claude install"):
        result = subprocess.run(
            ["graphify", "claude", "install"], cwd=project, capture_output=True, text=True
        )
    ui.raw(result.stdout)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("graphify claude install failed")
        return False
    ui.ok("Claude Code wired up (CLAUDE.md + hooks)")

    if not (desktop or trust_desktop):
        return True

    if not DESKTOP_CONFIG.exists():
        ui.error("no Claude Desktop config — is it installed?", DESKTOP_CONFIG)
        return False

    backup_path = DESKTOP_CONFIG.with_name(DESKTOP_CONFIG.name + f".bak-{_timestamp()}")
    shutil.copy(DESKTOP_CONFIG, backup_path)
    ui.ok("backup saved to", backup_path)

    data = json.loads(DESKTOP_CONFIG.read_text(encoding="utf-8"))

    if desktop:
        graphify_mcp = find_exe("graphify-mcp")
        server_name = f"graphify-{slug(project.name)}"
        data.setdefault("mcpServers", {})
        data["mcpServers"][server_name] = {
            "command": graphify_mcp,
            "args": ["--graph", str(project / "graphify-out" / "graph.json")],
        }
        ui.ok(f"MCP server '{server_name}' registered ->", project / "graphify-out/graph.json")

    if trust_desktop:
        prefs = data.setdefault("preferences", {})
        trusted = prefs.setdefault("localAgentModeTrustedFolders", [])
        if str(project) not in trusted:
            trusted.append(str(project))
            ui.ok("added to localAgentModeTrustedFolders (existing entries preserved):", project)
        else:
            ui.info("already in localAgentModeTrustedFolders")

    DESKTOP_CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    ui.hint("restart Claude Desktop to apply the changes")
    return True


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


# ------------------------------------------------------------- scheduling ---

def schedule(
    project: Path,
    *,
    interval_minutes: float = 15,
    slug_override: str | None = None,
    load: bool = False,
) -> bool:
    """Write (and optionally load) the LaunchAgent that re-runs `brain sync`.

    Refuses on a project with no registered connectors: a sync loop with
    nothing to sync is a silent no-op that is confusing to debug months later.
    """
    if not load_registry_entries(project):
        ui.error("no connectors registered — nothing worth scheduling yet")
        return False

    name = slug_override or slug(project.name)
    brain_exe = find_exe("brain")
    log_dir = project / "connectors" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_text = (TEMPLATE_DIR / "launchd_template.plist").read_text(encoding="utf-8")
    plist_text = (
        plist_text.replace("__PROJECT_SLUG__", name)
        .replace("__BRAIN_EXE__", brain_exe)
        .replace("__PROJECT_PATH__", str(project))
        .replace("__INTERVAL_SECONDS__", str(int(interval_minutes * 60)))
        .replace("__LOG_DIR__", str(log_dir))
    )

    plist_path = Path.home() / "Library/LaunchAgents" / f"com.graphify.sync.{name}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_text, encoding="utf-8")
    ui.ok("wrote", plist_path)

    load_cmd = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    if not load:
        ui.hint("not loaded yet — to activate it:", " ".join(load_cmd))
        return True

    result = subprocess.run(load_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("launchctl bootstrap failed")
        return False
    ui.ok(f"loaded with launchctl, runs every {interval_minutes:g} min")
    return True
