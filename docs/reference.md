# Reference

Every command and flag, what a brain looks like on disk, and how to write a connector for a system brainiphy
doesn't already know.

The [README](../README.md) is the place to start if you just want to use the tool — `brain` on its own does
everything below without any of it.

- [Commands](#commands)
- [Writing a connector](#writing-a-connector)
- [What a brain looks like on disk](#what-a-brain-looks-like-on-disk)

## Commands

### `brain` / `brain new [project]`

The guided flow, and the front door. It opens on the list of brains this machine knows about; opening one drops
you into its checklist. Two shortcuts skip the list: passing a path, and standing inside a brain already. Either
way the folder gets added to the list, which is how brains made before the list existed find their way onto it.

`t` opens the tools — sync, full rebuild, status, credentials, presets. `b` goes back to the brains list.

Needs a terminal, because it asks questions. In a script, use the individual commands below.

### `brain list` / `brain add [folder]` / `brain forget <folder>`

```
brain    setup  sources           graph        last sync  where
acme     7/7    2                 2431 nodes   2h ago     ~/clients
clinica  4/7    2 (1 unfinished)  not built    never      ~/clients
```

Nothing in that table is cached. Every column is read off the brain itself when the list is drawn, because a
stored copy would go stale exactly when it matters — and then there would be two answers to "is this brain
built". A folder that has moved or been deleted is reported as `missing` rather than quietly dropped.

`add` registers a brain that already exists — one made before this list did, or one that arrived with a cloned
repo. At a terminal, run it with no argument to browse for the folder.

`forget` removes a brain from the list and **touches nothing inside it**. The connectors, the graph and the
mirrored documents all stay exactly where they are, and `brain add` puts the entry back. Deleting a brain for
real is `rm`, on purpose.

### `brain view [project]`

Opens graphify's interactive graph in your browser. If the picture is older than the graph it is redrawn first
with `graphify cluster-only --no-label`, which costs no tokens — worth knowing because `graphify extract` writes
`graph.json` without redrawing `graph.html`, so after a full rebuild the picture would otherwise silently be the
previous one.

### `brain guide [project] [--verbose]`

Prints the seven steps and works out from the project on disk which are already done, what's missing from the
pending ones, and the exact next command to run. Read-only and safe to run anywhere — including from an agent
that needs to know where a brain stands without guessing.

```
$ brain guide ~/clients/acme

4/7 done   ✓ done  ▸ next  ○ pending  – n/a

 ✓ 1  Install graphify
 ✓ 2  Scaffold the project
 ✓ 3  Add data sources
 ▸ 4  Finish the custom connectors
       not runnable yet: hubspot (needs code)
       write the fetching for anything generated from a stub, and fill in the
       account details a preset needs
       $EDITOR ~/clients/acme/connectors/hubspot/sync.py
 ✓ 5  Run the first sync
 ○ 6  Connect it to Claude
       not connected yet
       brain connect-claude ~/clients/acme --desktop --trust-desktop
 ○ 7  Keep it in sync
       not scheduled — sync is manual for now
       brain schedule ~/clients/acme --interval-minutes 15 --load

↳ next step:
    $EDITOR ~/clients/acme/connectors/hubspot/sync.py
```

Steps can be done out of order, which is why 5 is ticked while 4 isn't. `--verbose` also shows the details of
the steps already completed.

### `brain init [project]`

Prepares a folder to receive connectors.

- Creates `connectors/registry.yaml` and `connectors/state/`
- Appends generated-output entries to `.gitignore` (`connectors/state/`, `connectors/logs/`, `mirrors/`,
  `raw/`, `graphify-out/`)
- Appends `connectors/` to `.graphifyignore`, so graphify doesn't index your connector scripts as source code
- Warns if `graphify` isn't installed

Run it with no `project` in a terminal and it opens an interactive picker instead of assuming a path:

```
╭─ Where do you want to create the brain? ─────────────────────────╮
│                                                                  │
│     ✓ Use this folder   ~/Documents                              │
│     ＋ Create a new folder here   you name it, it gets created   │
│                                                                  │
│   ❯  Clients/   already a brain                                  │
│      Estudios/                                                   │
│      Projects/                                                   │
│      personal/                                                   │
│                                                                  │
│    ↑↓ move  ↵ choose  → open folder  ← up                        │
│    / type a path  q cancel                                       │
│                                                                  │
╰────────────────────────────────────────────────── ~/Documents ───╯
```

It starts at `~/Documents`. The two things you actually came to do — use this folder, make a new one — are rows
of the list rather than hidden keys, so `↵` takes whichever row is highlighted and `→` is what descends into a
folder. Folders that are already brains say so. `/` accepts a path you type or paste, and nothing is created on
disk until you confirm. Where the terminal can't be put into raw mode it falls back to a numbered, typed
listing that needs only line input.

When stdin/stdout isn't a terminal (piped, cron, launchd), `project` still defaults to `.` — the picker never
blocks a script. Safe to re-run either way: it never overwrites an existing registry.

> [!IMPORTANT]
> `.gitignore` and `.graphifyignore` are **not** interchangeable, and they overlap in a way that bites.
> `.graphifyignore` is the one graphify always obeys — it's what keeps your connector *scripts* from being
> indexed as content. But graphify also honors `.gitignore`, where `brain init` puts `raw/` so mirrored content
> never gets committed. That's why the full `graphify extract` pass `brain sync` makes always passes
> `--no-gitignore`: without it, graphify skips the entire corpus and reports an empty project. (The incremental
> `graphify update` pass does not take the flag.) Don't skip `brain init` on an existing project just because
> `registry.yaml` is already there.

### `brain presets`

Lists the systems brainiphy ships a finished connector for, and the account details each one needs. Install one
with `brain new-connector <project> <name> --preset <preset>`.

### `brain new-connector <project> <name> [--interval-minutes N] [--mirror FOLDER] [--preset NAME] [--api URL] [--var K=V]`

Writes `connectors/<name>/sync.py` and registers it in `registry.yaml` with its type and the given interval
(default: 60).

```bash
brain new-connector ~/clients/acme hubspot --interval-minutes 30
brain new-connector ~/clients/acme docs --mirror ~/Dropbox/acme
brain new-connector ~/clients/acme crm --preset gohighlevel --var LOCATION_ID=abc123
brain new-connector ~/clients/acme billing --api https://api.example.com
```

| Flag | Effect |
| --- | --- |
| *(none)* | The generic template. Implement `fetch_records()`, then register any credential with `brain secret set`. |
| `--mirror FOLDER` | A **complete** connector that mirrors a local folder with `rsync -a --delete`. Nothing to implement — it works on the next `brain sync`. |
| `--preset NAME` | A finished connector for a system brainiphy already knows. `brain presets` lists them and the account details each one needs. |
| `--api URL` | A REST API with no preset. Pagination, retries, backoff and scope handling come from `httpclient.py`; you write one `collect_*` function per object. |
| `--var K=V` | Fills a constant in the generated script (`--var LOCATION_ID=abc123`). Applied last, so it can also override a computed default such as `SECRET_ITEM`. |

Reach for `--api` before the bare template for anything REST: a connector that hand-rolls `urllib`
re-introduces the four bugs `httpclient.py` exists to prevent — a banned default user-agent, a second unguarded
fetch for the next page, no retry on transient faults, and a missing scope reported as an auth failure.

A generated API or preset connector can be probed against the live API before you sync anything: it reports
which objects the credential can actually read and writes nothing. That's the fastest way to find out whether a
token has the scopes you assumed.

Existing scripts are never overwritten.

Why `--mirror` copies rather than symlinks: graphify doesn't follow symlinks, so a linked folder is simply never
indexed. `--delete` keeps it idempotent — files removed at the source disappear from the brain instead of
lingering as stale nodes. The mirror connector also converts the file types graphify can't read (CSV, JSON) into
one Markdown record per row, and names in its summary anything still unreadable — a `.numbers` file, a video —
rather than leaving it silently out of the graph.

### `brain sync [project] [--dry-run] [--full] [--backend NAME]`

Runs every connector whose interval has elapsed (tracked in `connectors/state/<name>.json`), then rebuilds the
graph — but only if at least one connector actually ran, or `--full` was passed.

| Flag | Effect |
| --- | --- |
| `--dry-run` | Report which connectors are due and whether their scripts exist. Runs nothing, touches nothing. |
| `--full` | Force a full re-index, and rebuild even if nothing was due. Implied on the first build. |
| `--backend NAME` | Force a specific LLM backend for the indexing pass. |

Prints `ran=[...] skipped=[...] errors=[...] graph_rebuilt=<bool>` and exits non-zero if any connector failed.
Safe to run against an empty registry.

**Two rebuild commands, and picking the wrong one silently does nothing** — `brain sync` picks for you:

| | indexes | needs an LLM | when brain uses it |
| --- | --- | --- | --- |
| `graphify extract` | documents **and** code | yes, for documents | first build, and every `--full` |
| `graphify update` | code only (local AST) | no | every later manual run |

A brain made of documents therefore needs a model to index it — **but not an API key**. `brain sync` chooses the
backend in this order:

1. `--backend <name>`, if you pass one.
2. Whichever API key is in the environment (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, …).
3. Your **Claude Code subscription**, when neither is set and the `claude` CLI is installed. graphify shells out
   to `claude -p`, so the indexing is billed to your Pro/Max plan instead of pay-as-you-go API credit.

```bash
brain sync ~/clients/acme --full --backend claude-cli   # force the subscription
export GRAPHIFY_CLAUDE_CLI_MODEL=haiku                  # faster + lighter than the Opus default
```

The subscription backend runs one chunk at a time, so a large first sync is slower than an API key would be. A
**ChatGPT subscription cannot be used this way** — graphify has no Codex/ChatGPT-CLI backend, and its `openai`
backend wants a real key. The other no-cost routes are a local model (`--backend ollama`) or running `/graphify`
inside a Claude session so the agent extracts the graph itself.

### `brain connect-claude [project] [--desktop] [--trust-desktop]`

| Flag | Effect |
| --- | --- |
| *(none)* | Runs `graphify claude install` — connects Claude Code via `CLAUDE.md` + hooks. The low-risk default. |
| `--desktop` | Also registers a `graphify-mcp` MCP server in `claude_desktop_config.json`, pointed at the project's `graph.json`. |
| `--trust-desktop` | Appends the project to `localAgentModeTrustedFolders`. **Additive** — existing entries are never replaced. |

`claude_desktop_config.json` is backed up (`.bak-<timestamp>`) before any edit. Restart Claude Desktop to pick
up changes.

### `brain schedule [project] --interval-minutes N [--load]`

Generates a LaunchAgent at `~/Library/LaunchAgents/com.graphify.sync.<slug>.plist` that runs
`brain sync <project> --full` on an interval. Logs land in `connectors/logs/`.

Without `--load` it only writes the plist and prints the `launchctl bootstrap` command; with `--load` it
activates immediately. Refuses to run if the project has no registered connectors — scheduling a sync loop with
nothing to sync is a silent no-op that is confusing to debug later.

The `--full` in the scheduled command is load-bearing: the incremental pass never indexes documents, so without
it an unattended brain would mirror new files in and report success while the graph quietly stopped being
current. It's also cheap — graphify gates the pass behind its own manifest and semantic cache, so a run with
nothing changed costs about a second and no tokens.

### `brain secret set <item>` / `brain secret get <item>`

Connector credentials, stored in the macOS Keychain.

```bash
brain secret set graphify-acme-hubspot   # prompts with hidden input
brain secret get graphify-acme-hubspot   # debugging only; prints bare, for piping
```

Connectors read credentials at runtime via `keychain.get_secret()`. Secrets never reach `registry.yaml`, CLI
arguments, or shell history. The write is confirmed by reading it back, because `security`'s exit code cannot be
trusted — it returns 0 on paths that store nothing, and on one that stores a *different* value.

### `brain status [project]`

Registered sources — type, whether each can actually run, when it next will, how many records it has pulled in
— then the graph size in nodes and edges, and where the setup stands.

"Ready" answers the question people are really asking. A stub and a preset missing its account id are both
present on disk and neither can run, so the column says which kind of not-ready it is rather than leaving you to
open files and find out.

## Writing a connector

`brain new-connector` generates a script that already satisfies the contract. In most cases you only fill in
`fetch_records()`:

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

Each record needs `id`, `title` and `body`; any other keys are written into the Markdown frontmatter. The
template's `main()` handles `--out`, normalization and stable file naming.

**The contract**, if you ever write one from scratch:

- Accept `--out <dir>` and write normalized Markdown there via `frontmatter.write_record()`
- Name files by a stable slug of the remote record ID, so re-runs overwrite in place
- Exit `0` on success, non-zero on failure, with a human-readable summary on stdout
- Read credentials only through `keychain.get_secret()` — never as a CLI argument and never hardcoded, since
  shell history, process listings and launchd logs would all leak them

Nothing in `sync.py` branches on what kind of connector a script is. At run time every connector is just an
executable that satisfies the contract above, which is why supporting a new kind of source means writing a
script and never extending the orchestrator.

### Choosing an approach, cheapest first

1. **A local folder already on disk** → `brain new-connector <project> <name> --mirror <folder>`. Generated
   complete; nothing to write.
2. **Content reachable by public URL** → `graphify add <url>` directly; no connector needed. Rebuild with
   `brain sync --full` afterwards.
3. **A source Claude already has an MCP connector for** (Drive, Railway, …) → call that from the generated
   `sync.py` rather than building fresh auth.
4. **A REST API** → `--api <base-url>`, then one `collect_*` function per kind of object.
5. **Anything else** (a database, a local export) → the bare template, as above.

`brain` asks which type a source is, does 1 and 2 for you, and records the answer as the connector's `type` so
every later screen can tell you what a source actually is.

## What a brain looks like on disk

A brain is an ordinary folder. This is everything `brain` puts in it:

```
<project>/
├── connectors/
│   ├── registry.yaml         # which connectors exist + their type and interval
│   ├── <name>/sync.py        # one script per data source
│   ├── state/<name>.json     # last-run timestamps, drives interval checks
│   └── logs/                 # LaunchAgent stdout/stderr
├── raw/<name>/               # connector output — normalized Markdown
├── graphify-out/graph.json   # the built graph
├── .graphifyignore           # excludes connectors/ from indexing
└── .gitignore
```

`registry.yaml` is meant to be readable and is safe to edit by hand — a malformed entry is skipped rather than
taking down the whole sync. Everything under `state/`, `logs/`, `raw/` and `graphify-out/` is generated and
gitignored.
