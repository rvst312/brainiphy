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
import shlex
import shutil
import site
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import yaml

from brainiphy_cli import brains, presets, sync as sync_mod, ui

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


# What kind of source a connector pulls from, recorded in registry.yaml as
# `type:` and shown wherever connectors are listed. It is display metadata, not
# behavior: `brain sync` still just executes sync.py and has no notion of
# connector types (see sync.py). It exists because "crm, docs, billing" tells
# nobody what those three actually are, and the answer used to be readable only
# by opening the generated script.
LOCAL_FOLDER = "local-folder"
HTTP_API = "http-api"
CUSTOM = "custom"

TYPE_LABELS = {
    LOCAL_FOLDER: "local folder",
    HTTP_API: "http api",
    CUSTOM: "custom",
}


def type_label(entry: dict, project: Path | None = None) -> str:
    """Human-readable type for a registry entry.

    Falls back to reading the generated script for connectors created before
    `type:` was recorded, so an existing brain does not show a column of
    dashes. A preset stores its own name as the type — "gohighlevel" says more
    than "http api" does.
    """
    kind = entry.get("type")
    if not kind and project is not None:
        kind = _infer_type(project, entry.get("name", ""))
    if not kind:
        return "—"
    return TYPE_LABELS.get(kind, kind)


def _infer_type(project: Path, name: str) -> str | None:
    script = project / "connectors" / name / "sync.py"
    if not name or not script.exists():
        return None
    text = script.read_text(encoding="utf-8", errors="ignore")
    if "MIRROR_SOURCE" in text:
        return LOCAL_FOLDER
    if "BASE_URL" in text:
        return HTTP_API
    return CUSTOM


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


def graphify_install_argv() -> list[str]:
    """How to install graphify *next to `brain`*, as an argv.

    Never a bare `pip3`. `brain sync` locates graphify with a PATH lookup and
    then shells out to it, so the two have to live under one interpreter — and
    `pip3` is whichever one happens to be first on PATH, which is exactly how
    they end up apart. The failure is not the install: it is a later `brain
    sync` reporting graphify as missing on a machine where it is plainly
    installed.

    `sys.executable` is the interpreter running `brain`, by construction. Under
    the installer's virtualenv that is the whole answer; `--user` is added only
    outside a venv, where it is both required (PEP 668 refuses a bare install
    into a Homebrew or system Python) and safe, since there is no venv for it
    to escape.
    """
    argv = [sys.executable, "-m", "pip", "install", "graphifyy"]
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        argv.insert(4, "--user")
    return argv


def graphify_install_command() -> str:
    """`graphify_install_argv()` as a line a user can paste."""
    return shlex.join(graphify_install_argv())


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

    # The list of brains fills itself in as you work. Asking someone to
    # register a brain they just created would be a second step for one
    # intention, and the list is only useful if it is complete.
    if brains.remember(project):
        ui.ok("added to your brains, see them all with:", "brain list")


# ------------------------------------------------------------ visualizer ----

def open_graph(project: Path) -> bool:
    """Open graphify's interactive graph, refreshing it first if it is stale.

    `graphify extract` writes graph.json and does not regenerate graph.html —
    only `update` and `cluster-only` do — so after a full rebuild the picture
    is quietly the previous one, which is worse than no picture at all. The
    refresh uses `cluster-only --no-label`: it redraws from the graph on disk
    without asking a model to name the communities, so opening the visualizer
    never costs tokens.
    """
    graph = project / "graphify-out" / "graph.json"
    if not graph.exists():
        ui.warn("no graph yet — there is nothing to draw")
        ui.hint("build it with:", f"brain sync {ui.short_path(project)} --full")
        return False

    html = project / "graphify-out" / "graph.html"
    if brains.visualizer_is_stale(project):
        # Distinguish the two, because "older than the graph" is simply untrue
        # the first time and leaves you wondering what you missed. A brain
        # built only with `graphify extract` has no picture at all: extract
        # writes graph.json and never graph.html.
        ui.info("drawing the graph for the first time (no model needed)" if not html.exists()
                else "the picture is older than the graph, redrawing it (no model needed)")
        try:
            graphify = find_exe("graphify")
        except FileNotFoundError:
            ui.error("graphify not found, cannot redraw")
            return False
        with ui.working("redrawing the graph"):
            result = subprocess.run(
                [graphify, "cluster-only", str(project), "--no-label"],
                capture_output=True, text=True)
        if result.returncode != 0 or not html.exists():
            ui.raw(result.stdout)
            ui.raw(result.stderr, stderr=True)
            if not html.exists():
                ui.error("could not draw the graph")
                return False
            ui.warn("redraw failed, opening the previous picture")

    if not html.exists():
        ui.error("graphify has not written a picture for this graph yet")
        ui.hint("rebuild it with:", f"brain sync {ui.short_path(project)} --full")
        return False

    webbrowser.open(html.as_uri())
    ui.ok("opened in your browser:", html)
    return True


# ---------------------------------------------------- connector creation ----

def set_constant(text: str, name: str, expr: str) -> tuple[str, bool]:
    """Rewrite a module-level `NAME = …` assignment to `NAME = <expr>`.

    Templates declare everything the installer must supply as a top-level
    constant, so filling one in is a line rewrite rather than string surgery on
    a placeholder. A lambda supplies the replacement because re.sub would treat
    backslashes in a Windows-ish path as escape sequences.
    """
    pattern = re.compile(rf"^{re.escape(name)} = .*$", re.MULTILINE)
    new_text, count = pattern.subn(lambda _m: f"{name} = {expr}", text, count=1)
    return new_text, bool(count)


def unfilled_placeholders(text: str) -> list[str]:
    """Constants still left at their REPLACE_ME value, in file order. A
    connector with any of these cannot run yet, and steps.py reports it."""
    return re.findall(r'^([A-Z_][A-Z0-9_]*) = "REPLACE_ME[A-Z0-9_]*"$', text, re.MULTILINE)


def create_connector(
    project: Path,
    name: str,
    *,
    interval_minutes: float = 60,
    mirror: Path | None = None,
    preset: str | None = None,
    api_base: str | None = None,
    variables: dict[str, str] | None = None,
) -> Path | None:
    """Write connectors/<name>/sync.py from the right template and register it.

    Four kinds of connector, in descending order of how much is already done:
      - `preset`   a finished connector for a system brainiphy knows (see
                   presets/): only the account-identifying constants are left.
      - `mirror`   the rsync template for a folder on this Mac — nothing to fill in.
      - `api_base` the REST template: plumbing done, endpoints left to write.
      - neither    the generic template, a bare fetch_records() to implement.

    Returns the script path, or None when a script was already there (never
    overwritten — it may hold real work) or the preset name was unknown.
    """
    connector_dir = project / "connectors" / name
    script_path = connector_dir / "sync.py"

    if script_path.exists():
        ui.error("already exists, not overwriting it:", script_path)
        return None

    chosen: presets.Preset | None = None
    if preset:
        chosen = presets.get(preset)
        if chosen is None:
            ui.error(f"unknown preset {preset!r}. Available:", ", ".join(presets.names()))
            return None
        template = chosen.text()
    elif mirror:
        template = (TEMPLATE_DIR / "mirror_template.py").read_text(encoding="utf-8")
    elif api_base:
        template = (TEMPLATE_DIR / "api_template.py").read_text(encoding="utf-8")
    else:
        template = (TEMPLATE_DIR / "connector_template.py").read_text(encoding="utf-8")

    # A preset names its own source system (the vendor, not this connector's
    # local nickname) so records keep the same source_system across projects.
    if not chosen:
        template, _ = set_constant(template, "SOURCE_SYSTEM", repr(name))

    if mirror:
        template, _ = set_constant(template, "MIRROR_SOURCE", f"Path({str(mirror)!r})")
    if api_base:
        template, _ = set_constant(template, "BASE_URL", repr(api_base.rstrip("/")))
    if chosen or api_base:
        template, _ = set_constant(template, "SECRET_ITEM",
                                   repr(secret_item_name(project, name)))

    for key, value in (variables or {}).items():
        template, replaced = set_constant(template, key, repr(value))
        if not replaced:
            ui.warn(f"{key} is not a constant in this template, ignored")

    connector_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(template, encoding="utf-8")
    script_path.chmod(0o755)
    ui.ok("created", script_path.relative_to(project))

    if chosen:
        ui.info(f"preset: {chosen.title}")
    missing = unfilled_placeholders(template)
    if missing:
        ui.warn("still to fill in before it can run: " + ", ".join(missing))

    # A preset records its own name as the type: "gohighlevel" identifies the
    # source far better than the generic "http api" it would otherwise get.
    kind = (
        chosen.name if chosen
        else LOCAL_FOLDER if mirror
        else HTTP_API if api_base
        else CUSTOM
    )

    entries = load_registry_entries(project)
    if any(e.get("name") == name for e in entries):
        ui.info(f"{name} was already in registry.yaml, not duplicating it")
    else:
        entries.append({"name": name, "type": kind, "interval_minutes": interval_minutes})
        write_registry_entries(project, entries)
        ui.ok(f"registered {name} ({TYPE_LABELS.get(kind, kind)}) in registry.yaml, "
              f"every {interval_minutes:g} min")

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

# launchd's own default, and all a job gets when the plist says nothing.
_LAUNCHD_DEFAULT_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def _agent_path() -> str:
    """PATH to bake into the LaunchAgent.

    The tools a sync reaches for — `brain`, `graphify`, and `claude` when the
    graph is indexed through the Claude Code subscription — normally live in a
    user bin dir that only the login shell puts on PATH. A scheduled run has no
    login shell, so their directories are resolved here, while `brain schedule`
    still has the user's environment, and pinned into the plist.

    Kept unresolved on purpose: `claude` is typically a symlink into a
    versioned install dir (~/.local/share/claude/versions/2.1.241), and that
    directory holds version-named files, not a binary called `claude`. Pinning
    the resolved parent would write a PATH entry that provides nothing and goes
    stale on the next Claude Code update; the symlink's own directory does not.
    """
    dirs: list[str] = []
    for exe in ("brain", "graphify", "claude"):
        found = shutil.which(exe)
        if not found:
            continue
        parent = str(Path(found).parent)
        if parent not in dirs:
            dirs.append(parent)
    return ":".join([*dirs, _LAUNCHD_DEFAULT_PATH])


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
        .replace("__PATH__", _agent_path())
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
