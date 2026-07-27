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
brain new-connector /tmp/some-test-project docs --mirror /tmp/some-source-folder
brain presets
brain new-connector /tmp/some-test-project crm --preset gohighlevel --var LOCATION_ID=abc123
brain new-connector /tmp/some-test-project api --api https://api.example.com
brain sync /tmp/some-test-project --dry-run
brain guide /tmp/some-test-project
brain status /tmp/some-test-project
```

A generated API/preset connector can be exercised without a full sync: `<project>/connectors/<name>/sync.py
--out /tmp/probe --probe` hits the real API and reports which objects the credential can read, writing
nothing. `--only <collector>` narrows it further. That is the fastest way to check a change to `httpclient.py`
or `collect.py` against a live API.

The app (`brain` / `brain new`) can't be exercised that way — it refuses without a TTY, and piping answers into it through
`script -q /dev/null` doesn't work either (the pty eats stdin). Drive `app.run()` from a Python snippet that replaces
`keys.supported`, `picker.is_interactive`, `keys.read_key` and the `prompt.*` functions with scripted
answers instead; that covers the whole flow including the real subprocess calls.

Driving it means feeding keypresses. Use `app.run()` with
`keys.supported`, `picker.is_interactive` and `keys.read_key` replaced, feeding an iterator of key names, and
stub `ui.clear` so the screens stay in the scrollback:
```python
from brainiphy_cli import keys, picker, menu, ui
keys.supported = lambda: True; picker.is_interactive = lambda: True; ui.clear = lambda: None
seq = iter(["1", " ", "q"])          # open Status, dismiss it, quit
keys.read_key = lambda: next(seq)
menu.run("/path/to/brain")
```

## Architecture

**`src/brainiphy_cli/cli.py`** — argparse entry point (`brain` console script). Each subcommand (`menu`, `new`, `guide`, `init`, `presets`, `new-connector`, `sync`, `connect-claude`, `schedule`, `secret set/get`, `status`) is a standalone `cmd_*` function; there's no shared command base class or plugin system to look for. Bare `brain` (no subcommand) opens the menu, which is why the subparsers are `required=False`.

A subparser marked `set_defaults(framed=True)` has its output drawn inside the app box by `main()`. Three groups are deliberately unmarked: anything that prompts mid-run (`new`, `init` without a path, `secret set`) — a framed block shows nothing until it ends, so a prompt inside one is invisible; `sync`, which streams for minutes; and `secret get`, which must stay bare on stdout to be pipeable. Framing is additionally gated on `ui.out.is_terminal`, so `brain status > file` still writes plain text rather than box-drawing characters. Keep it as argparse plumbing only — the actual work belongs in `project.py`/`sync.py`/`app.py`/`actions.py`, so the guided flow and the individual commands can't drift apart. All user-facing output is in English (it was Spanish until the repo was translated — don't reintroduce Spanish strings).

**`src/brainiphy_cli/steps.py`** — the machine-readable version of the playbook `SKILL.md` describes in prose: seven ordered `Step`s, and `inspect(project)` which resolves each one against what's on disk. `brain guide` renders it, `brain status` uses it for its next-step line, and the app walks it. **If the process changes, it changes here and in SKILL.md together** — this is the copy a user actually sees. Notes:
- `render()` and `render_status()` live here rather than in cli.py so `app.py` shows the same screens the commands print, without cli.py and app.py importing each other.
- A step's `done` covers both `DONE` and `SKIP` (not applicable, e.g. "implement the connectors" on a brain that only mirrors folders) — `SKIP` renders as a dash, and must not block the next-step pointer.
- Step 4 counts a connector as unfinished for two different reasons, and says which: a `raise NotImplementedError` left by a stub template, or a `CONST = "REPLACE_ME"` in a preset whose account details were never supplied. Both are detected as source text, never by importing the script — a half-written connector may not import at all.
- Detection is read-only and cheap; it runs on every `brain status`. Don't add subprocess calls to it.

**`src/brainiphy_cli/app.py`** — bare `brain` (and `brain new`): the flow. The home screen *is* the seven steps — a checklist you run in place, which re-inspects and advances as steps complete. It replaced a flat action menu and a one-shot wizard, which between them meant three overlapping front doors and no single answer to "how do I drive this". Notes:
- Owns no operations. Each step's action calls the same `project.py`/`sync.py`/`actions.py` function the equivalent named command calls.
- The step list is **not** written here — it comes from `steps.inspect()`, and `STEP_ACTIONS` only maps a step's `key` to how it is performed. Adding or reordering a step stays a single edit in `steps.py`; the flow follows.
- Steps stay reachable out of order on purpose. A new brain wants the sequence; a brain six months old wants "add one more source", and making it walk the flow to get there would be worse than the menu this replaced.
- `_append_wrapped()` exists because Rich wraps to the panel width but starts continuation lines at column 0, so a step's explanation collides with the list above it. Wrap against `ui.out.width - ui.FRAME_CHROME - indent` instead.
- Actions render through `ui.framed()`, which boxes their output. `sync` deliberately does not — nothing appears until a framed block ends, and watching a sync run matters more than the border.
- The credentials screen reads `SECRET_ITEM` out of the connector's `sync.py` rather than assuming `secret_item_name()`; a connector may legitimately point at a differently-named item, and writing the conventional one instead would store a credential the script never reads.
- Refuses without a TTY (`picker.is_interactive()` **and** `keys.supported()`), same as `brain new`.

**`src/brainiphy_cli/keys.py`** — single-keypress reading via termios/tty, for the menu's arrow navigation (`prompt.py` reads whole lines and cannot do this). Restores terminal settings in a `finally` on every path — leaving a shell in raw mode looks like a broken terminal, which is far worse than a broken menu. In raw mode Ctrl-C arrives as byte `0x03` rather than SIGINT, so it is re-raised as `KeyboardInterrupt` to keep cancellation handling uniform. Three things here are load-bearing and all three were found by testing it under a real pty, not by reading it:
- `tty.setraw(fd, termios.TCSANOW)` — the default `TCSAFLUSH` **discards pending input**, and the menu re-enters raw mode between every keypress, so anything typed while it repaints would vanish.
- `os.read(fd, 1)`, never `sys.stdin.read(1)` — `sys.stdin` buffers in userspace, so a whole escape sequence arriving at once sits in that buffer, `select()` on the fd reports nothing pending, and an arrow key is misread as Esc plus two stray characters. Holding an arrow key down does precisely this.
- `_pushback` — the byte read while disambiguating `Esc` from an escape sequence has already left the fd and cannot be un-read, so Esc-then-another-key would swallow the second key.

**`src/brainiphy_cli/actions.py`** — the interactive operations a step performs (`add_preset`, `add_local_folder`, `add_url`, `add_api`, `add_custom`, `ensure_graphify`, `_store_secret`) plus the `Cancelled` exception and the prompt wrappers that raise it. This was `wizard.py` until the flow moved into `app.py`: the step *ordering* went with it, the operations stayed. It must not import `app.py` — the dependency runs one way.

**`src/brainiphy_cli/project.py`** — every operation performed *on a target project*: `scaffold()`, `create_connector()`, `connect_claude()`, `schedule()`, plus the registry read/write helpers and `find_exe()`. `create_connector()` picks one of four templates (`preset` → `mirror` → `api_base` → the bare stub) and fills them in through `set_constant()`, which rewrites a whole `NAME = …` line rather than doing string surgery on a placeholder — so a value only has to be a valid Python literal, not escape-safe inside quotes. `--var` is applied last and can therefore override a computed default such as `SECRET_ITEM`. cli.py, app.py and actions.py all call these. Each prints its own progress through `ui` and returns a plain bool/path; exit codes are cli.py's job.

**`src/brainiphy_cli/prompt.py`** — `ask` / `confirm` / `choose` / `ask_path`, on the same Rich console as `ui` (a prompt drawn on a different console doesn't line up with the output around it). Every one returns `None` when the user hits Ctrl-C/Ctrl-D, so cancellation is an ordinary value instead of an exception at each call site. `ui.py` stays output-only.

**`src/brainiphy_cli/ui.py`** — the single Rich `Console` pair (`ui.out` / `ui.err`) plus the icon helpers every other module prints through: `step/ok/info/warn/error/hint/header/table/working`. Rules worth keeping:
- Messages are built as `rich.text.Text`, never markup strings, and `highlight=False` — connector names and paths come from user-written files and a stray `[` would otherwise be parsed as a markup tag. Use `ui.cell()` for table cells for the same reason.
- `ui.error()` is the only helper that writes to stderr; keep failures there so `brain sync` stays pipeable.
- Subprocess output (graphify, connector scripts) goes through `ui.raw()`, which disables markup/highlight and uses `soft_wrap` so tracebacks stay copy-pasteable.
- `cmd_secret_get` deliberately uses a bare `print()` — the value is meant to be piped, so it must stay unstyled and alone on stdout.
- Rich already drops color for non-TTY output and honors `NO_COLOR`; don't add a `--no-color` flag for it.
- `framed()` is how the menu puts the whole app in a box without every command knowing about it: it captures **both** consoles (so a `ui.error()` on stderr lands inside the border rather than escaping it), narrows them by the panel's 4 chrome columns first (otherwise text wraps to the terminal width and then wraps again inside the border), and restores everything in a `finally` so a raised exception still prints what was produced. `working()` no-ops while capturing — a spinner would only write animation frames into the captured text. Don't wrap long-running work in it: nothing appears until the block ends.

**`src/brainiphy_cli/sync.py`** — the orchestrator `brain sync` calls. Deliberately has no notion of "connector types": every connector is just an executable script conforming to a contract (see below), so adding support for a new kind of data source means writing a new `sync.py` in the target project, never extending this module. Key logic:
- `load_registry()` reads `<project>/connectors/registry.yaml`.
- `is_due()` / `_mark_ran()` track last-run timestamps per connector in `<project>/connectors/state/<name>.json` — this is how polling intervals are enforced across separate `brain sync` invocations (e.g. from a LaunchAgent).
- `run()` shells out to each due connector's `sync.py --out <project>/raw/<name>/`, then calls `build_graph()` once at the end if anything actually ran or `full=True` (not on every invocation — avoids needless rebuilds).
- `build_graph()` picks between two graphify commands that are **not** interchangeable: `graphify extract` (full pass, the only one that indexes documents, needs an LLM backend) on the first build and on `--full`, `graphify update` (code-only local AST, no key, a silent no-op on a document corpus) after that. It always passes `--no-gitignore` — graphify honors `.gitignore`, which lists `raw/`, so without the flag it skips the whole corpus and reports an empty project. It also detects the "no LLM API key" failure and prints the two ways out (export a key, or run `/graphify` inside Claude Code) instead of a bare non-zero exit.
- `find_graphify()` resolves the `graphify` binary via PATH then falls back to the current interpreter's `--user` site bin dir.

**`src/brainiphy_cli/httpclient.py`** — the network layer generated API connectors import (like they already import `frontmatter`/`keychain`). `HttpClient` + the `NoScope` exception. Four things are baked in because each was learned by getting it wrong against a live API, and each fails *silently* rather than loudly:
- A non-default `User-Agent`. urllib's `Python-urllib/3.x` is banned by the WAF in front of several SaaS APIs (Cloudflare error 1010) — a 403 that never reaches the vendor and reads like an auth problem.
- One network entry point. `paginate()` fetches the next page through the same `request()` as the first, on purpose: the bug it exists to prevent is a second bare `urlopen` for `nextPageUrl` that skips all the retry and error handling.
- Retries on 429/5xx *and* on transient network faults (DNS, TLS handshake, dropped read). Connectors run unattended under launchd, where a blip must not become a stale graph.
- 401/403 raises `NoScope` rather than erroring. Missing scope is the normal shape of a vendor token, not a failure.

**`src/brainiphy_cli/collect.py`** — the run loop for connectors that pull more than one kind of object. A connector declares `Collector(name, subfolder, fetch)` entries and calls `collect.run()`, which gives it `--probe` (report what the credential can read, write nothing), `--only`, per-object isolation, and an exit code that distinguishes "no scope" (0, normal) from a real failure (1). Collectors run in list order and may depend on it — e.g. GHL's pipelines collector fills the id→name lookup its opportunities collector reads, so a stage renders as `"Awaiting payment"` and not `f7a80aa4-…`. Order the list accordingly and degrade gracefully when the earlier one had no scope.

**`src/brainiphy_cli/presets/`** — finished connectors for systems brainiphy already knows, installed with `--preset <name>`. `__init__.py` holds the `PRESETS` registry (`Preset` + the `Variable`s the installer must supply); each `<name>.py` is a complete connector copied *as text* and never imported, so the `CONST = "REPLACE_ME"` placeholders in it are fine. Adding one is: drop the file, register it. `gohighlevel.py` is the reference implementation — a preset keeps its own vendor `SOURCE_SYSTEM` (so records carry the same provenance across projects) rather than being renamed to the local connector name.

**`src/brainiphy_cli/api_template.py`** — the template for a REST API with no preset (`--api <base-url>`). Thin on purpose: `HttpClient` and `collect.run()` do the plumbing, and what's left is one `collect_*` function per object. Keeps a `raise NotImplementedError` so `steps.py` still detects it as unfinished.

**`src/brainiphy_cli/mirror_template.py`** — the folder-mirroring template, copied by `create_connector(..., mirror=<folder>)`. Unlike `connector_template.py` it is complete: an `rsync -a --delete` of a local folder into `--out`, with `MIRROR_SOURCE` substituted as a `repr()`'d literal. Mirroring rather than symlinking is forced by graphify (`follow_symlinks=False`, no CLI flag); `--delete` is what makes re-runs idempotent instead of leaving stale nodes behind.

**`src/brainiphy_cli/connector_template.py`** — the fallback template, used when no `--preset`/`--mirror`/`--api` fits (a database, a local export, anything that isn't an HTTP API). Reach for `--api` first for anything REST — a connector that hand-rolls `urllib` is re-introducing the four bugs `httpclient.py` exists to prevent. This file also states the contract every connector script must satisfy, whichever template it came from:
- Accept `--out <dir>`, write normalized Markdown+frontmatter via `frontmatter.write_record()`.
- File names are a stable slug of the remote record ID, so re-runs overwrite in place rather than duplicating graph nodes.
- Exit 0/non-zero, human-readable summary on stdout.
- Read credentials only via `keychain.get_secret(<item>)` — never accept a secret as a CLI arg or hardcode one (shell history / process listings / launchd logs would leak it).
- Implementing a new source means filling in `fetch_records()` in the generated file — nothing else in the template should normally change.

**`src/brainiphy_cli/frontmatter.py`** — `write_record()` / `slugify()` / `yaml_str()`. Produces the same Markdown+YAML-frontmatter shape graphify's own `graphify add` produces, including the same hostile-string escaping (mirrors graphify's `ingest.py _yaml_str`, since a connector might pipe in untrusted field values like a CRM record title). Any connector-generated file must go through this, not hand-rolled YAML.

**`src/brainiphy_cli/keychain.py`** — thin wrapper over `/usr/bin/security` (macOS Keychain generic passwords). `get_secret()` is the only thing connector scripts should call; `set_secret()` is for `brain secret set` itself. Secrets never touch `registry.yaml` or chat context — this boundary is intentional, don't add a code path that lets a secret value flow through an argument or a file brain writes.

**`src/brainiphy_cli/picker.py`** — the directory browser (`pick_project_dir()`), used by `cmd_init` with no path and by the menu when the cwd is not already a brain. This is the first screen most people see, so it wears the same chrome as the menu: arrow-key navigation inside `ui.app_panel`, scrolling viewport, and an "already a brain" marker next to folders that are scaffolded. It only creates a folder after an explicit confirmation.

Two implementations, and the second is not dead code: `_pick_navigable` needs raw mode, so when `keys.supported()` is false it falls back to `_pick_typed`, the original numbered/typed loop that needs only line input. Losing the picker altogether would make `brain init` with no argument unusable.

`cmd_init` calls it *only* when `picker.is_interactive()` (stdin **and** stdout are TTYs); piped or launchd-driven invocations keep the old behavior of defaulting to the cwd, so the argparse default for `project` is `None`, not `"."` — don't restore `"."` or the interactive path becomes unreachable.

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
`.gitignore` vs `.graphifyignore` serve different purposes here, and they interact: `.graphifyignore` is the one graphify always obeys (its own gitignore-syntax-compatible parser) and is what keeps `connectors/` out of the index. But graphify also honors `.gitignore` unless `--no-gitignore` is passed — and `.gitignore` lists `raw/`, so any graphify call over a brain without that flag finds nothing at all. Verified empirically: `graphify extract` on a scaffolded project reports "found 0 code, 0 docs" without the flag and finds the corpus with it.

## Key constraints worth knowing before changing behavior

- graphify does not follow symlinks (`detect.py`, `follow_symlinks=False`, no CLI flag) — any code path that's tempted to symlink a local source folder into a project instead needs to physically mirror it (e.g. `rsync -a --delete`).
- `connect-claude --trust-desktop` must always be additive to `localAgentModeTrustedFolders` in `claude_desktop_config.json`, never a replace — and the config file must be backed up before every edit (see `cmd_connect_claude`'s backup-then-write pattern).
- `brain schedule` intentionally refuses to run when a project has zero registered connectors (`cmd_schedule`'s early check) — don't remove that guard, it exists because scheduling a sync loop with nothing to sync is a silent no-op that's confusing to debug later.
