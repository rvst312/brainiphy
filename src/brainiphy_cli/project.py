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

import ast
import hashlib
import json
import math
import plistlib
import os
import re
import shlex
import shutil
import site
import subprocess
import sys
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from xml.parsers.expat import ExpatError

import yaml

from brainiphy_cli import brains, hostos, presets, sync as sync_mod, systemd, ui
from brainiphy_cli.storage import atomic_write, contained_path, exclusive_lock, validate_name

TEMPLATE_DIR = Path(__file__).resolve().parent

REGISTRY_HEADER = """\
# Connector registry for this brain. Format consumed by `brain sync`.
#
# Every entry requires a script at connectors/<name>/sync.py — use
# `brain new-connector <project> <name>` to create one from the template.
"""

GITIGNORE_ENTRIES = [
    "connectors/state/",
    "connectors/.sync.lock",
    "connectors/.identity.lock",
    "connectors/.graph-pending.json",
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
    if kind is not None and not isinstance(kind, str):
        return "invalid type"
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
    sibling = Path(sys.executable).parent / name
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
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
    existing_lines = {line.strip() for line in existing.splitlines()}
    missing = [line for line in entries if line not in existing_lines]
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
    atomic_write(registry_path, REGISTRY_HEADER + "\n" + body)


# ------------------------------------------------------------- scaffold ----

def scaffold(project: Path) -> None:
    """Create connectors/, registry.yaml and the two ignore files. Idempotent —
    safe to re-run on a project that is already half set up."""
    project.mkdir(parents=True, exist_ok=True)
    contained_path(project, "connectors", "state").mkdir(parents=True, exist_ok=True)
    project_namespace(project, create=True)

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
    try:
        validate_name(name)
        if not math.isfinite(interval_minutes) or interval_minutes <= 0:
            raise ValueError("interval_minutes must be finite and positive")
        script_path = contained_path(project, "connectors", name, "sync.py")
        entries = load_registry_entries(project)
        if any(not isinstance(e, dict) or not e.get("name") for e in entries):
            raise ValueError("repair unnamed entries in registry.yaml before adding a connector")
        if mirror:
            source = mirror.resolve()
            output = contained_path(project, "raw", name).resolve()
            if source == output or source in output.parents or output in source.parents:
                raise ValueError("mirror source and output must not overlap")
    except (ValueError, OSError, yaml.YAMLError) as exc:
        ui.error(str(exc))
        return None
    connector_dir = script_path.parent

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
    project_namespace(project, create=True)
    if not mirror:
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

    if any(e.get("name") == name for e in entries):
        ui.info(f"{name} was already in registry.yaml, not duplicating it")
    else:
        entries.append({"name": name, "type": kind, "interval_minutes": interval_minutes})
        write_registry_entries(project, entries)
        ui.ok(f"registered {name} ({TYPE_LABELS.get(kind, kind)}) in registry.yaml, "
              f"every {interval_minutes:g} min")

    return script_path


def project_namespace(project: Path, *, create: bool = False) -> str:
    """Stable resource namespace; inspection never creates it.

    A fresh identifier separates same-named clients and reused paths. Persisting
    it keeps it stable when the folder is subsequently moved or renamed. The
    read-only fallback for legacy brains is provisional until initialization.
    """
    identity = contained_path(project, "connectors", "identity.json")
    if create:
        with exclusive_lock(contained_path(project, "connectors", ".identity.lock")):
            if identity.exists():
                return project_namespace(project)
            value = f"{slug(project.name)[:48]}-{uuid.uuid4().hex}"
            atomic_write(identity, json.dumps({"namespace": value}) + "\n")
            return value
    if identity.exists():
        data = json.loads(identity.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "namespace" not in data:
            raise ValueError(f"invalid brain identity at {identity}; restore it before making changes")
        value = data["namespace"]
        return validate_name(value)
    digest = hashlib.sha256(str(project.resolve()).encode("utf-8")).hexdigest()[:32]
    value = f"{slug(project.name)[:48]}-{digest}"
    return value


def secret_item_name(project: Path, connector: str) -> str:
    """Unique default; existing scripts may deliberately name another item."""
    validate_name(connector)
    return f"graphify-{project_namespace(project)}-{connector}"


def connector_secret_item(project: Path, connector: str) -> str:
    script = contained_path(project, "connectors", validate_name(connector), "sync.py")
    if script.exists():
        try:
            tree = ast.parse(script.read_text(encoding="utf-8"))
            for node in tree.body:
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "SECRET_ITEM" for t in node.targets)
                        and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
                        and "REPLACE_ME" not in node.value.value):
                    return node.value.value
        except (SyntaxError, ValueError):
            pass
    return secret_item_name(project, connector)


def migrate_identity(project: Path) -> bool:
    """Copy legacy credentials and update conventional template references.

    Explicitly named custom credentials are kept. No Keychain item is deleted;
    a shared legacy item may still be needed by another brain.
    """
    from brainiphy_cli import keychain

    if not brains.is_brain(project):
        ui.error("not a scaffolded brain:", project)
        return False
    project_namespace(project, create=True)
    ok = True
    for entry in load_registry_entries(project):
        try:
            name = validate_name(entry.get("name") if isinstance(entry, dict) else None)
        except ValueError as exc:
            ui.error(str(exc))
            ok = False
            continue
        script = contained_path(project, "connectors", name, "sync.py")
        if not script.exists():
            continue
        old = f"graphify-{slug(project.name)}-{name}"
        new = secret_item_name(project, name)
        if connector_secret_item(project, name) != old:
            continue
        try:
            # Never replace a credential already set for the unique namespace.
            try:
                keychain.get_secret(new)
            except keychain.SecretNotFoundError:
                keychain.set_secret(new, keychain.get_secret(old))
            text = script.read_text(encoding="utf-8")
            updated, replaced = set_constant(text, "SECRET_ITEM", repr(new))
            if not replaced:
                raise ValueError(f"cannot safely rewrite SECRET_ITEM in {script}")
            atomic_write(script, updated)
            ui.ok(f"{name}: migrated credential reference to {new}")
        except (keychain.SecretNotFoundError, keychain.SecretWriteError, ValueError) as exc:
            ui.error(str(exc))
            ok = False
    ui.info("legacy Keychain items were retained; probe each connector to verify its account")
    return ok


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
            [find_exe("graphify"), "claude", "install"], cwd=project, capture_output=True, text=True
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
        server_name = f"graphify-{project_namespace(project, create=True)}"
        data.setdefault("mcpServers", {})
        graph_path = str(project / "graphify-out" / "graph.json")
        for existing_name, config in list(data["mcpServers"].items()):
            if (existing_name != server_name and existing_name.startswith("graphify-")
                    and isinstance(config, dict) and config.get("args") == ["--graph", graph_path]):
                del data["mcpServers"][existing_name]
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

    atomic_write(DESKTOP_CONFIG, json.dumps(data, indent=2))
    ui.hint("restart Claude Desktop to apply the changes")
    return True


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


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


def agent_belongs_to(path: Path, project: Path) -> bool:
    try:
        data = plistlib.loads(path.read_bytes())
        if not isinstance(data, dict):
            return False
        args = data.get("ProgramArguments", [])
        return (len(args) >= 3 and args[1] == "sync"
                and Path(args[2]).resolve() == project.resolve())
    except (OSError, ValueError, TypeError, KeyError, ExpatError, plistlib.InvalidFileException):
        return False


def launch_agent(project: Path) -> Path | None:
    """The installed schedule for this project: a LaunchAgent plist on macOS,
    a systemd timer on Linux."""
    if not hostos.is_macos():
        return systemd.find(project)
    folder = Path.home() / "Library/LaunchAgents"
    return next((p for p in sorted(folder.glob("com.graphify.sync.*.plist"))
                 if agent_belongs_to(p, project)), None)


def _agent_has_namespace(path: Path, namespace: str) -> bool:
    try:
        data = plistlib.loads(path.read_bytes())
        return isinstance(data, dict) and data.get("BrainiphyNamespace") == namespace
    except (OSError, ValueError, ExpatError, plistlib.InvalidFileException):
        return False


def schedule(
    project: Path,
    *,
    interval_minutes: float = 15,
    slug_override: str | None = None,
    load: bool = False,
) -> bool:
    """Write (and optionally load) the job that re-runs `brain sync`: a
    LaunchAgent on macOS, a systemd timer on Linux (systemd.py).

    Refuses on a project with no registered connectors: a sync loop with
    nothing to sync is a silent no-op that is confusing to debug months later.
    """
    if not load_registry_entries(project):
        ui.error("no connectors registered — nothing worth scheduling yet")
        return False

    if not math.isfinite(interval_minutes) or interval_minutes * 60 < 1:
        ui.error("interval_minutes must be finite and at least one second")
        return False
    try:
        name = validate_name(slug_override) if slug_override else project_namespace(project, create=True)
    except ValueError as exc:
        ui.error(str(exc))
        return False
    brain_exe = find_exe("brain")
    if not hostos.is_macos():
        return systemd.schedule(
            project, name=name, namespace=project_namespace(project, create=True),
            brain_exe=brain_exe, interval_minutes=interval_minutes,
            explicit_name=bool(slug_override), load=load)
    log_dir = project / "connectors" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Parse the template first, then replace values as data, never raw XML.
    template = (TEMPLATE_DIR / "launchd_template.plist").read_text(encoding="utf-8")
    data = plistlib.loads(template.replace("__INTERVAL_SECONDS__", "1").encode("utf-8"))
    substitutions = {"__PROJECT_SLUG__": name, "__BRAIN_EXE__": brain_exe,
                     "__PROJECT_PATH__": str(project), "__PATH__": _agent_path(),
                     "__LOG_DIR__": str(log_dir)}
    def fill(value):
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, str):
            return re.sub(r"__[A-Z_]+__", lambda m: substitutions.get(m.group(), m.group()), value)
        return value
    data = fill(data)
    data["StartInterval"] = int(interval_minutes * 60)
    namespace = project_namespace(project, create=True)
    data["BrainiphyNamespace"] = namespace
    plist_path = Path.home() / "Library/LaunchAgents" / f"com.graphify.sync.{name}.plist"
    # Keep an existing job's label if it belongs to this exact project.
    existing = launch_agent(project)
    if existing is None:
        existing = next((p for p in sorted(plist_path.parent.glob("com.graphify.sync.*.plist"))
                         if _agent_has_namespace(p, namespace)), None)
    if existing and not slug_override:
        plist_path = existing
        data["Label"] = plistlib.loads(existing.read_bytes())["Label"]
    if (plist_path.exists() and not agent_belongs_to(plist_path, project)
            and not _agent_has_namespace(plist_path, namespace)):
        ui.error("schedule name is already used by another project:", plist_path)
        return False
    atomic_write(plist_path, plistlib.dumps(data).decode("utf-8"))
    ui.ok("wrote", plist_path)

    load_cmd = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    if not load:
        ui.hint("not loaded yet — to activate it:", " ".join(load_cmd))
        return True

    service = f"gui/{os.getuid()}/{data['Label']}"
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True, text=True)
    if loaded.returncode == 0:
        stopped = subprocess.run(["launchctl", "bootout", service], capture_output=True, text=True)
        if stopped.returncode != 0:
            ui.raw(stopped.stderr, stderr=True)
            ui.error("could not unload the existing job; new schedule has not been loaded")
            return False
    result = subprocess.run(load_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        ui.raw(result.stderr, stderr=True)
        ui.error("launchctl bootstrap failed")
        return False
    ui.ok(f"loaded with launchctl, runs every {interval_minutes:g} min")
    return True
