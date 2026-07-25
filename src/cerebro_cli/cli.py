"""cerebro — CLI for bootstrapping and maintaining graphify knowledge-graph
brains for a business, from zero data sources to a synced graph connected
to Claude Code and Claude Desktop.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import site
import subprocess
import sys
from pathlib import Path

import yaml

from cerebro_cli import keychain, sync as sync_mod

TEMPLATE_DIR = Path(__file__).resolve().parent

REGISTRY_HEADER = """\
# Registro de conectores para este cerebro. Formato consumido por `cerebro sync`.
#
# Cada entrada requiere un script en connectors/<name>/sync.py — usa
# `cerebro new-connector <project> <name>` para crearlo desde la plantilla.
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
# data they produce in raw//mirrors/. Verified against detect.py: gitignore
# entries alone do NOT stop graphify from scanning a path, only
# .graphifyignore does (it has its own, gitignore-syntax-compatible parser).
GRAPHIFYIGNORE_ENTRIES = [
    "connectors/",
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def _append_ignore_entries(path: Path, entries: list[str], header_comment: str) -> int:
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


def _find_exe(name: str) -> str:
    on_path = shutil.which(name)
    if on_path:
        return on_path
    candidate = Path(site.getuserbase()) / "bin" / name
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"{name} not found on PATH or in {Path(site.getuserbase()) / 'bin'}")


# ---------------------------------------------------------------- init ----

def cmd_init(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    project.mkdir(parents=True, exist_ok=True)
    connectors_dir = project / "connectors"
    (connectors_dir / "state").mkdir(parents=True, exist_ok=True)

    registry_path = connectors_dir / "registry.yaml"
    if registry_path.exists():
        print(f"[init] {registry_path} ya existe, no lo toco")
    else:
        registry_path.write_text(REGISTRY_HEADER + "\nconnectors: []\n", encoding="utf-8")
        print(f"[init] creado {registry_path}")

    added = _append_ignore_entries(
        project / ".gitignore", GITIGNORE_ENTRIES, "# cerebro-empresarial: salidas generadas, no versionar"
    )
    print(f"[init] .gitignore: {added} entradas nuevas" if added else "[init] .gitignore ya cubre todo, no lo toco")

    added = _append_ignore_entries(
        project / ".graphifyignore",
        GRAPHIFYIGNORE_ENTRIES,
        "# cerebro-empresarial: no indexar los scripts de conectores como codigo fuente",
    )
    print(f"[init] .graphifyignore: {added} entradas nuevas" if added else "[init] .graphifyignore ya cubre todo, no lo toco")

    try:
        graphify = _find_exe("graphify")
        print(f"[init] graphify encontrado: {graphify}")
    except FileNotFoundError:
        print("[init] graphify no está instalado. Instálalo con: pip3 install --user graphifyy", file=sys.stderr)

    print(f"[init] listo. Siguiente paso: cerebro new-connector {project} <nombre>")
    return 0


# --------------------------------------------------------- new-connector --

def _load_registry_entries(registry_path: Path) -> list[dict]:
    if not registry_path.exists():
        return []
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    return data.get("connectors") or []


def _write_registry_entries(registry_path: Path, entries: list[dict]) -> None:
    body = yaml.safe_dump({"connectors": entries}, sort_keys=False, default_flow_style=False)
    registry_path.write_text(REGISTRY_HEADER + "\n" + body, encoding="utf-8")


def cmd_new_connector(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    name = args.name
    connector_dir = project / "connectors" / name
    script_path = connector_dir / "sync.py"

    if script_path.exists():
        print(f"[new-connector] {script_path} ya existe, no lo sobreescribo", file=sys.stderr)
        return 1

    connector_dir.mkdir(parents=True, exist_ok=True)
    template = (TEMPLATE_DIR / "connector_template.py").read_text(encoding="utf-8")
    template = template.replace('SOURCE_SYSTEM = "REPLACE_ME"', f'SOURCE_SYSTEM = "{name}"')
    script_path.write_text(template, encoding="utf-8")
    script_path.chmod(0o755)
    print(f"[new-connector] creado {script_path}")

    registry_path = project / "connectors" / "registry.yaml"
    entries = _load_registry_entries(registry_path)
    if any(e.get("name") == name for e in entries):
        print(f"[new-connector] {name} ya estaba en registry.yaml, no duplico")
    else:
        entries.append({"name": name, "interval_minutes": args.interval_minutes})
        _write_registry_entries(registry_path, entries)
        print(f"[new-connector] {name} añadido a {registry_path} (cada {args.interval_minutes} min)")

    print(f"[new-connector] siguiente paso: edita {script_path} (fetch_records) y, si hace falta credencial:")
    print(f"  cerebro secret set graphify-{_slug(project.name)}-{name}")
    return 0


# ------------------------------------------------------------------ sync --

def cmd_sync(args: argparse.Namespace) -> int:
    report = sync_mod.run(Path(args.project), dry_run=args.dry_run)
    if args.dry_run:
        return 0
    print(f"[sync] ran={report.ran} skipped={report.skipped} errors={report.errors} graph_rebuilt={report.graph_rebuilt}")
    return 1 if report.errors else 0


# --------------------------------------------------------- connect-claude --

def cmd_connect_claude(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()

    result = subprocess.run(["graphify", "claude", "install"], cwd=project, capture_output=True, text=True)
    print(result.stdout.rstrip())
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1

    if not (args.desktop or args.trust_desktop):
        return 0

    config_path = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if not config_path.exists():
        print(f"[connect-claude] {config_path} no existe — ¿está instalado Claude Desktop?", file=sys.stderr)
        return 1

    backup_path = config_path.with_name(config_path.name + f".bak-{_timestamp()}")
    shutil.copy(config_path, backup_path)
    print(f"[connect-claude] backup guardado en {backup_path}")

    data = json.loads(config_path.read_text(encoding="utf-8"))

    if args.desktop:
        graphify_mcp = _find_exe("graphify-mcp")
        server_name = f"graphify-{_slug(project.name)}"
        data.setdefault("mcpServers", {})
        data["mcpServers"][server_name] = {
            "command": graphify_mcp,
            "args": ["--graph", str(project / "graphify-out" / "graph.json")],
        }
        print(f"[connect-claude] servidor MCP '{server_name}' registrado -> {project / 'graphify-out/graph.json'}")

    if args.trust_desktop:
        prefs = data.setdefault("preferences", {})
        trusted = prefs.setdefault("localAgentModeTrustedFolders", [])
        if str(project) not in trusted:
            trusted.append(str(project))
            print(f"[connect-claude] {project} añadido a localAgentModeTrustedFolders (se conservan las demás)")
        else:
            print(f"[connect-claude] {project} ya estaba en localAgentModeTrustedFolders")

    config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("[connect-claude] reinicia Claude Desktop para aplicar los cambios")
    return 0


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d%H%M%S")


# -------------------------------------------------------------- schedule --

def cmd_schedule(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    registry_path = project / "connectors" / "registry.yaml"
    entries = _load_registry_entries(registry_path)
    if not entries:
        print("[schedule] no hay conectores registrados — no tiene sentido programar nada todavía", file=sys.stderr)
        return 1

    slug = args.slug or _slug(project.name)
    cerebro_exe = _find_exe("cerebro")
    log_dir = project / "connectors" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_text = (TEMPLATE_DIR / "launchd_template.plist").read_text(encoding="utf-8")
    plist_text = (
        plist_text.replace("__PROJECT_SLUG__", slug)
        .replace("__CEREBRO_EXE__", cerebro_exe)
        .replace("__PROJECT_PATH__", str(project))
        .replace("__INTERVAL_SECONDS__", str(int(args.interval_minutes * 60)))
        .replace("__LOG_DIR__", str(log_dir))
    )

    plist_path = Path.home() / "Library/LaunchAgents" / f"com.graphify.sync.{slug}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_text, encoding="utf-8")
    print(f"[schedule] escrito {plist_path}")

    load_cmd = ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)]
    if args.load:
        result = subprocess.run(load_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1
        print(f"[schedule] cargado con launchctl (cada {args.interval_minutes} min)")
    else:
        print("[schedule] no cargado todavía. Para activarlo:")
        print(f"  {' '.join(load_cmd)}")
    return 0


# ---------------------------------------------------------------- secret --

def cmd_secret_set(args: argparse.Namespace) -> int:
    value = getpass.getpass(f"Valor para {args.item} (no se mostrará): ")
    if not value:
        print("[secret] valor vacío, cancelado", file=sys.stderr)
        return 1
    keychain.set_secret(args.item, value)
    print(f"[secret] guardado {args.item} en el Keychain")
    return 0


def cmd_secret_get(args: argparse.Namespace) -> int:
    try:
        print(keychain.get_secret(args.item))
    except keychain.SecretNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- status --

def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    entries = _load_registry_entries(project / "connectors" / "registry.yaml")
    print(f"Proyecto: {project}")
    print(f"Conectores registrados: {len(entries)}")
    for entry in entries:
        name = entry["name"]
        interval = float(entry.get("interval_minutes", 60))
        due = sync_mod.is_due(project, name, interval)
        script_ok = (project / "connectors" / name / "sync.py").exists()
        print(f"  - {name}: cada {interval}min, {'due ahora' if due else 'al día'}, script={'ok' if script_ok else 'FALTA'}")

    graph_json = project / "graphify-out" / "graph.json"
    if graph_json.exists():
        try:
            data = json.loads(graph_json.read_text(encoding="utf-8"))
            nodes = len(data.get("nodes", []))
            edges = len(data.get("links", data.get("edges", [])))
            print(f"Grafo: {nodes} nodos, {edges} aristas ({graph_json})")
        except json.JSONDecodeError:
            print(f"Grafo: {graph_json} existe pero no se pudo leer")
    else:
        print("Grafo: no construido todavía (corre: graphify update .)")
    return 0


# ------------------------------------------------------------------ main --

def main() -> int:
    parser = argparse.ArgumentParser(prog="cerebro", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Prepara connectors/ y .gitignore en un proyecto")
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-connector", help="Crea un conector nuevo desde la plantilla")
    p.add_argument("project")
    p.add_argument("name")
    p.add_argument("--interval-minutes", type=float, default=60)
    p.set_defaults(func=cmd_new_connector)

    p = sub.add_parser("sync", help="Corre los conectores debidos y reconstruye el grafo")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("connect-claude", help="Conecta el proyecto a Claude Code / Desktop")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--desktop", action="store_true", help="Registra un servidor MCP en Claude Desktop")
    p.add_argument("--trust-desktop", action="store_true", help="Añade el proyecto a localAgentModeTrustedFolders (aditivo)")
    p.set_defaults(func=cmd_connect_claude)

    p = sub.add_parser("schedule", help="Genera e instala un LaunchAgent para `cerebro sync` periódico")
    p.add_argument("project", nargs="?", default=".")
    p.add_argument("--interval-minutes", type=float, default=15)
    p.add_argument("--slug", default=None)
    p.add_argument("--load", action="store_true", help="Cargar inmediatamente con launchctl")
    p.set_defaults(func=cmd_schedule)

    secret = sub.add_parser("secret", help="Gestiona credenciales en el Keychain")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True)
    sp = secret_sub.add_parser("set")
    sp.add_argument("item")
    sp.set_defaults(func=cmd_secret_set)
    sp = secret_sub.add_parser("get")
    sp.add_argument("item")
    sp.set_defaults(func=cmd_secret_get)

    p = sub.add_parser("status", help="Muestra el estado de los conectores y el grafo")
    p.add_argument("project", nargs="?", default=".")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
