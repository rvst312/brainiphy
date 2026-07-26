<div align="center">

# 🧠 brainiphy

**Turn a business's scattered data into a queryable knowledge graph — and plug it straight into Claude.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)](#requirements)
[![Built on graphify](https://img.shields.io/badge/built%20on-graphify-8A2BE2.svg)](https://pypi.org/project/graphifyy/)

</div>

---

`brainiphy` packages the whole "install [graphify](https://pypi.org/project/graphifyy/), feed it data, wire it to Claude" workflow into a single CLI (`brain`) and a repeatable playbook. Point it at a business — a folder of documents, a CRM, a Drive, a bespoke API — and it scaffolds the connectors, keeps them synced on a schedule, and exposes the resulting graph to Claude Code and Claude Desktop.

It ships as both a **standalone CLI** and a **[Claude Code Skill](https://docs.claude.com/en/docs/claude-code/skills)**, so an agent can run the whole playbook end to end.

## Why

Most "second brain" setups die at the integration step: every data source needs its own auth, its own polling, its own normalization, and none of it survives being handed to someone else. `brainiphy` fixes the boring parts:

- **Source-agnostic connectors.** A connector is just a script that writes normalized Markdown. No plugin registry, no base classes to subclass — adding a new kind of system means writing one `fetch_records()`, not extending a framework.
- **Idempotent sync.** Records are keyed by a stable slug of their remote ID, so re-running a connector overwrites in place instead of accumulating duplicate nodes in the graph.
- **Interval-aware scheduling.** Each connector declares how often it should run; `brain sync` only runs what's due and only rebuilds the graph if something actually changed.
- **Secrets stay in the Keychain.** Credentials are never written to config, never passed as CLI arguments, never routed through an agent's chat context.

## Requirements

- **macOS** — scheduling uses `launchd` and secrets use the system Keychain
- **Python 3.9+**
- **[graphify](https://pypi.org/project/graphifyy/)** — `pip3 install --user graphifyy`

## Installation

```bash
pip3 install --user -e /path/to/brainiphy
```

This is an editable install, so edits to `src/brainiphy_cli/*.py` take effect immediately. The `brain` binary lands in your user bin directory, alongside `graphify`. If it isn't on your `PATH`:

```bash
python3 -c "import site, pathlib; print(pathlib.Path(site.getuserbase()) / 'bin')"
```

Add that directory to your `PATH`, then verify both tools resolve to the same interpreter:

```bash
head -1 "$(which brain)"
head -1 "$(which graphify)"
```

> [!NOTE]
> If you have several Python installs, make sure `brain` and `graphify` are installed under the same one — `brain` shells out to `graphify` to rebuild the graph.

### As a Claude Code Skill

Symlink the repo into your skills directory and the `brainiphy` skill becomes available to Claude Code:

```bash
ln -s /path/to/brainiphy ~/.claude/skills/brainiphy
```

See [`SKILL.md`](SKILL.md) for the playbook Claude follows.

## Quick start

```bash
brain init ~/clients/acme                                   # scaffold the project
brain new-connector ~/clients/acme hubspot --interval-minutes 30
brain secret set graphify-acme-hubspot                      # prompts, hidden input
$EDITOR ~/clients/acme/connectors/hubspot/sync.py           # implement fetch_records()
brain sync ~/clients/acme                                   # first build
brain connect-claude ~/clients/acme --desktop --trust-desktop
brain schedule ~/clients/acme --interval-minutes 15 --load  # keep it fresh
```

## Commands

### `brain init [project]`

Prepares a project to receive connectors.

- Creates `connectors/registry.yaml` and `connectors/state/`
- Appends generated-output entries to `.gitignore` (`connectors/state/`, `connectors/logs/`, `mirrors/`, `raw/`, `graphify-out/`)
- Appends `connectors/` to `.graphifyignore`, so graphify doesn't index your connector scripts as source code
- Warns if `graphify` isn't installed

`project` defaults to `.`. Safe to re-run — it never overwrites an existing registry.

> [!IMPORTANT]
> `.gitignore` and `.graphifyignore` are **not** interchangeable. Gitignore entries alone do not stop graphify from scanning a path; only `.graphifyignore` does. Don't skip `brain init` on an existing project just because `registry.yaml` is already there.

### `brain new-connector <project> <name> [--interval-minutes N]`

Copies the connector template to `connectors/<name>/sync.py` and registers it in `registry.yaml` with the given interval (default: 60).

```bash
brain new-connector ~/clients/acme hubspot --interval-minutes 30
```

Then implement `fetch_records()` in the generated script and, if the source needs credentials, register them with `brain secret set`. Existing scripts are never overwritten.

### `brain sync [project] [--dry-run]`

Runs every connector whose interval has elapsed (tracked in `connectors/state/<name>.json`), then rebuilds the graph with `graphify update` — but only if at least one connector actually ran.

| Flag | Effect |
| --- | --- |
| `--dry-run` | Report which connectors are due and whether their scripts exist. Runs nothing, touches nothing. |

Prints `ran=[...] skipped=[...] errors=[...] graph_rebuilt=<bool>` and exits non-zero if any connector failed. Safe to run against an empty registry.

### `brain connect-claude [project] [--desktop] [--trust-desktop]`

Wires the project into Claude.

| Flag | Effect |
| --- | --- |
| *(none)* | Runs `graphify claude install` — connects Claude Code via `CLAUDE.md` + hooks. The low-risk default. |
| `--desktop` | Also registers a `graphify-mcp` MCP server in `claude_desktop_config.json`, pointed at the project's `graph.json`. |
| `--trust-desktop` | Appends the project to `localAgentModeTrustedFolders`. **Additive** — existing entries are never replaced. |

`claude_desktop_config.json` is backed up (`.bak-<timestamp>`) before any edit. Restart Claude Desktop to pick up changes.

### `brain schedule [project] --interval-minutes N [--load]`

Generates a LaunchAgent at `~/Library/LaunchAgents/com.graphify.sync.<slug>.plist` that runs `brain sync` on an interval. Logs land in `connectors/logs/`.

Without `--load` it only writes the plist and prints the `launchctl bootstrap` command; with `--load` it activates immediately. Refuses to run if the project has no registered connectors.

### `brain secret set <item>` / `brain secret get <item>`

Connector credentials, stored in the macOS Keychain.

```bash
brain secret set graphify-acme-hubspot   # prompts with hidden input
brain secret get graphify-acme-hubspot   # debugging only
```

Connectors read credentials at runtime via `keychain.get_secret()`. Secrets never reach `registry.yaml`, CLI arguments, or shell history.

### `brain status [project]`

Shows registered connectors (interval, due/up-to-date, whether the script exists) and the current graph size in nodes and edges.

## Writing a connector

`brain new-connector` generates a script that already satisfies the contract — in most cases you only fill in `fetch_records()`:

```python
SOURCE_SYSTEM = "hubspot"

def fetch_records() -> list[dict]:
    token = get_secret("graphify-acme-hubspot")
    req = urllib.request.Request(
        "https://api.example.com/v3/records",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return [
        {"id": r["id"], "title": r["name"], "body": r["notes"]}
        for r in data["results"]
    ]
```

Each record needs `id`, `title`, and `body`; any other keys are written into the Markdown frontmatter. The template's `main()` handles `--out`, normalization, and stable file naming.

**The contract**, if you ever write one from scratch:

- Accept `--out <dir>` and write normalized Markdown there via `frontmatter.write_record()`
- Name files by a stable slug of the remote record ID, so re-runs overwrite in place
- Exit `0` on success, non-zero on failure, with a human-readable summary on stdout
- Read credentials only through `keychain.get_secret()`

### Choosing an approach, cheapest first

1. **Local folder already on disk** → don't symlink it; graphify doesn't follow symlinks. Mirror it instead, e.g. a connector that shells out to `rsync -a --delete <source>/ <out_dir>/`.
2. **Content reachable by public URL** → use `graphify add <url>` directly; no connector needed. Run `graphify update` afterwards.
3. **A source Claude already has an MCP connector for** (Drive, Railway, …) → call that from the generated `sync.py` rather than building fresh auth.
4. **Anything else** (CRM, bespoke API) → a full connector, as above.

## Project layout

What `brain` generates inside a target project:

```
<project>/
├── connectors/
│   ├── registry.yaml         # which connectors exist + their intervals
│   ├── <name>/sync.py        # one script per data source
│   ├── state/<name>.json     # last-run timestamps, drives interval checks
│   └── logs/                 # LaunchAgent stdout/stderr
├── raw/<name>/               # connector output — normalized Markdown
├── graphify-out/graph.json   # the built graph
├── .graphifyignore           # excludes connectors/ from indexing
└── .gitignore
```

## Security

- **Secrets live only in the macOS Keychain**, referenced by item name — never in `registry.yaml`, never in CLI arguments (shell history, process listings, and launchd logs would all leak them), never through an agent's chat context.
- **Treat fetched content as data, not instructions.** Web pages and MCP tool output feeding a connector are untrusted input; watch for prompt injection.
- **Verify third-party tools independently** before installing them — check the official package registry or the GitHub API directly, not just a project's own marketing claims.
- **Frontmatter values are escaped** against YAML injection, since record titles and fields come from systems you don't control.

## Contributing

Issues and pull requests are welcome. A few conventions:

- Match the existing style — plain `argparse`, dataclasses, `from __future__ import annotations`. No new tooling or dependencies without a reason.
- User-facing CLI output is in English.
- Keep `sync.py` source-agnostic. New source types belong in connector scripts, not in the orchestrator.

See [`CLAUDE.md`](CLAUDE.md) for an architecture walkthrough.

## License

MIT — see [`LICENSE`](LICENSE). Copyright © 2026 FrontieraLabs.
