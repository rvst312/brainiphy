# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This repo *is* the source for a Claude Code Skill (`SKILL.md`) plus the Python package it depends on (`src/brainiphy_cli/`). It is symlinked at `~/.claude/skills/brainiphy` — editing files here takes effect immediately for any Claude session that invokes the skill, no reinstall needed for the skill file itself (the Python package does need an editable install, see below).

The skill's job: turn "install graphify, feed it data, wire it to Claude" into a repeatable playbook for bootstrapping a queryable knowledge-graph "brain" for a business/client, reused across client projects. `SKILL.md` is the playbook an agent follows; `brainiphy_cli` is the tool it runs.

Read `SKILL.md` first — it is the primary spec for both the CLI's behavior and the order of operations an agent should follow. This CLAUDE.md covers what SKILL.md doesn't: install/dev commands and code architecture.

## Install / dev commands

```
/usr/local/opt/python@3.11/bin/python3.11 -m pip install --user -e ~/.claude/skills/brainiphy
```

Editable install — required once so the `brain` binary exists on PATH; after that, edits to `src/brainiphy_cli/*.py` take effect immediately (no reinstall). Re-run it after touching `dependencies` in `pyproject.toml` (currently `pyyaml`, `rich`) — an editable install does not pick up new deps on its own, and a missing `rich` breaks every command, since `ui.py` imports it at module level. Confirm the interpreter actually used with `head -1 $(which graphify)` — this machine has multiple Python 3 installs and `brain`/`graphify` must both resolve to the same one, or `brain sync`'s `find_graphify()` / `_find_exe()` PATH lookups can pick the wrong one.

No test suite exists in this repo currently. There is no lint/format config either — match existing style (plain argparse, dataclasses, `from __future__ import annotations`) rather than introducing a new tool.

To exercise a change manually, run the CLI against a scratch directory:
```
brain init /tmp/some-test-project
brain new-connector /tmp/some-test-project demo --interval-minutes 5
brain sync /tmp/some-test-project --dry-run
brain status /tmp/some-test-project
```

## Architecture

**`src/brainiphy_cli/cli.py`** — argparse entry point (`brain` console script). Each subcommand (`init`, `new-connector`, `sync`, `connect-claude`, `schedule`, `secret set/get`, `status`) is a standalone `cmd_*` function; there's no shared command base class or plugin system to look for. All user-facing output is in English (it was Spanish until the repo was translated — don't reintroduce Spanish strings).

**`src/brainiphy_cli/ui.py`** — the single Rich `Console` pair (`ui.out` / `ui.err`) plus the icon helpers every other module prints through: `step/ok/info/warn/error/hint/header/table/working`. Rules worth keeping:
- Messages are built as `rich.text.Text`, never markup strings, and `highlight=False` — connector names and paths come from user-written files and a stray `[` would otherwise be parsed as a markup tag. Use `ui.cell()` for table cells for the same reason.
- `ui.error()` is the only helper that writes to stderr; keep failures there so `brain sync` stays pipeable.
- Subprocess output (graphify, connector scripts) goes through `ui.raw()`, which disables markup/highlight and uses `soft_wrap` so tracebacks stay copy-pasteable.
- `cmd_secret_get` deliberately uses a bare `print()` — the value is meant to be piped, so it must stay unstyled and alone on stdout.
- Rich already drops color for non-TTY output and honors `NO_COLOR`; don't add a `--no-color` flag for it.

**`src/brainiphy_cli/sync.py`** — the orchestrator `brain sync` calls. Deliberately has no notion of "connector types": every connector is just an executable script conforming to a contract (see below), so adding support for a new kind of data source means writing a new `sync.py` in the target project, never extending this module. Key logic:
- `load_registry()` reads `<project>/connectors/registry.yaml`.
- `is_due()` / `_mark_ran()` track last-run timestamps per connector in `<project>/connectors/state/<name>.json` — this is how polling intervals are enforced across separate `brain sync` invocations (e.g. from a LaunchAgent).
- `run()` shells out to each due connector's `sync.py --out <project>/raw/<name>/`, then calls `graphify update <project>` once at the end if anything actually ran (not on every invocation — avoids needless rebuilds).
- `find_graphify()` resolves the `graphify` binary via PATH then falls back to the current interpreter's `--user` site bin dir.

**`src/brainiphy_cli/connector_template.py`** — copied verbatim by `cmd_new_connector` into `<project>/connectors/<name>/sync.py` (with `SOURCE_SYSTEM` pre-filled). This is the contract every connector script must satisfy:
- Accept `--out <dir>`, write normalized Markdown+frontmatter via `frontmatter.write_record()`.
- File names are a stable slug of the remote record ID, so re-runs overwrite in place rather than duplicating graph nodes.
- Exit 0/non-zero, human-readable summary on stdout.
- Read credentials only via `keychain.get_secret(<item>)` — never accept a secret as a CLI arg or hardcode one (shell history / process listings / launchd logs would leak it).
- Implementing a new source means filling in `fetch_records()` in the generated file — nothing else in the template should normally change.

**`src/brainiphy_cli/frontmatter.py`** — `write_record()` / `slugify()` / `yaml_str()`. Produces the same Markdown+YAML-frontmatter shape graphify's own `graphify add` produces, including the same hostile-string escaping (mirrors graphify's `ingest.py _yaml_str`, since a connector might pipe in untrusted field values like a CRM record title). Any connector-generated file must go through this, not hand-rolled YAML.

**`src/brainiphy_cli/keychain.py`** — thin wrapper over `/usr/bin/security` (macOS Keychain generic passwords). `get_secret()` is the only thing connector scripts should call; `set_secret()` is for `brain secret set` itself. Secrets never touch `registry.yaml` or chat context — this boundary is intentional, don't add a code path that lets a secret value flow through an argument or a file brain writes.

**`src/brainiphy_cli/picker.py`** — Rich-rendered interactive directory browser (`pick_project_dir()`), used by `cmd_init` when `brain init` is run with no path. Starts at `~/Documents`, accepts a numbered pick / free-text filter / pasted path, and only creates the folder after an explicit confirmation. `cmd_init` calls it *only* when `picker.is_interactive()` (stdin **and** stdout are TTYs); piped or launchd-driven invocations keep the old behavior of defaulting to the cwd, so the argparse default for `project` is `None`, not `"."` — don't restore `"."` or the interactive path becomes unreachable.

**`src/brainiphy_cli/launchd_template.plist`** — placeholder-substituted (`__PROJECT_SLUG__`, `__BRAIN_EXE__`, etc.) by `cmd_schedule` into `~/Library/LaunchAgents/com.graphify.sync.<slug>.plist`, then optionally loaded with `launchctl bootstrap`. Generated output, not meant to be hand-edited — change the template and regenerate instead.

### Per-project generated layout

`brain init <project>` and friends produce, inside the *target* business/client project (not this repo):
```
<project>/connectors/registry.yaml       # which connectors exist + interval_minutes
<project>/connectors/<name>/sync.py      # one script per data source, from connector_template.py
<project>/connectors/state/<name>.json   # last-run timestamps, drives is_due()
<project>/raw/<name>/                    # connector output (normalized Markdown), graphify ingests from here
<project>/graphify-out/graph.json        # the built graph
<project>/.graphifyignore                # must list connectors/ — otherwise graphify's AST extractor
                                          # indexes the connector scripts themselves as source code
```
`.gitignore` vs `.graphifyignore` serve different purposes here: gitignore entries alone do **not** stop graphify from scanning a path, only `.graphifyignore` does (its own gitignore-syntax-compatible parser). Don't assume adding something to one covers the other.

## Key constraints worth knowing before changing behavior

- graphify does not follow symlinks (`detect.py`, `follow_symlinks=False`, no CLI flag) — any code path that's tempted to symlink a local source folder into a project instead needs to physically mirror it (e.g. `rsync -a --delete`).
- `connect-claude --trust-desktop` must always be additive to `localAgentModeTrustedFolders` in `claude_desktop_config.json`, never a replace — and the config file must be backed up before every edit (see `cmd_connect_claude`'s backup-then-write pattern).
- `brain schedule` intentionally refuses to run when a project has zero registered connectors (`cmd_schedule`'s early check) — don't remove that guard, it exists because scheduling a sync loop with nothing to sync is a silent no-op that's confusing to debug later.
