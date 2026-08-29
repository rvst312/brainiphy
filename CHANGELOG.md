# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Your brains.** `brain` now opens on the list of every brain on this
  machine — how far along each is, how many sources feed it, how big its graph
  is, when it last synced — and you open one, add one, or remove one from
  there. The same list is `brain list`, with `brain add` and `brain forget`
  behind it. Brains register themselves as you create them. Removing one only
  removes the entry: nothing inside the brain is touched, ever.
- `brain view` opens graphify's interactive graph in the browser, redrawing it
  first if it is older than the graph. `graphify extract` writes `graph.json`
  without redrawing `graph.html`, so the picture was quietly the previous one
  after every full rebuild. The redraw uses `cluster-only --no-label` and costs
  no tokens.
- `brain status` now says of each source whether it can actually run, and why
  not when it cannot ("needs code", "needs LOCATION_ID"), alongside how many
  records it has pulled in. A script being present never answered the question
  anyone was asking.
- Documents can be indexed with your **Claude Code subscription** instead of an
  API key. With no key configured and the `claude` CLI installed, `brain sync`
  picks graphify's `claude-cli` backend, which authenticates against your
  Pro/Max plan; `brain sync --backend <name>` forces a specific one. A first
  sync on a machine that has Claude Code installed no longer stops at "no LLM
  API key found".
- A unit suite (`python -m unittest discover -s tests`), run by CI beside the
  smoke test. It covers what fails silently: backend selection, which graphify
  command rebuilds the graph, interval enforcement, the slug and YAML escaping
  that keep a re-sync idempotent, what the checklist reads off disk, and the
  HTTP layer's retry and no-scope behavior.
- One-line install: `curl -fsSL .../install.sh | bash`. It builds a dedicated
  virtualenv, symlinks `brain` and `graphify` into `~/.local/bin`, adds that to
  the shell profile in a marked block if it is missing, registers the Claude
  Code skill, and reports whether an LLM backend is reachable. `--dry-run`
  shows what it would do; `--uninstall` reverses all of it. An existing
  checkout is updated with `git pull --ff-only`, and left alone entirely if it
  has uncommitted changes.
- `brain` (equivalently `brain new`) — the app: the seven steps of building a
  brain rendered as a checklist, each runnable in place, advancing to the next
  as they complete. Steps stay reachable out of order, and off-flow operations
  (sync, credentials, presets, switching project) sit behind `t` for tools.
  `brain init` at a terminal scaffolds and then continues into it.
- `brain guide` — shows those seven steps and how far along a given project is;
  the same data drives the next-step line in `brain status`.
- `brain new-connector --mirror <folder>` — a complete, ready-to-run connector
  that mirrors a local folder into the brain, with no `fetch_records()` to
  write.
- `brain sync --full` — full `graphify extract` re-index instead of the
  incremental, code-only `graphify update`. Happens automatically on the first
  build.
- `brain presets` and `brain new-connector --preset <name> [--var K=V]` —
  install a connector that is already written for a known system. GoHighLevel /
  LeadConnector is the first: contacts, opportunities, pipelines, conversations,
  calendars, users and forms from one sub-account.
- `brain new-connector --api <base-url>` — a connector for a REST API with the
  plumbing already done, leaving one `collect_*` function per object to write.
- Generated API connectors accept `--probe`, which reports what the credential
  can actually read without writing anything, and `--only <collector>`.
- Connectors record what kind of source they pull from as `type:` in
  `registry.yaml` — `local-folder`, `http-api`, `custom`, or the preset's own
  name — and it is shown by `brain status`, `brain guide`, `brain sync
  --dry-run` and the app. Brains created before the field existed infer it from
  the generated script. Display metadata only: nothing branches on it.
- Step 4 of the app is something you can act on rather than read: it opens a
  connector in `$EDITOR`, or probes the live API to report what the credential
  can actually read.
- Contributor workflow: `CONTRIBUTING.md`, commit template, PR and issue
  templates, `scripts/smoke.sh`, and CI running it on Linux and macOS across
  Python 3.9–3.13.

### Changed

- Commands that only print a result (`status`, `guide`, `presets`,
  `new-connector`, `connect-claude`, `schedule`) render inside the app box.
  Interactive commands, `sync` and `secret get` deliberately do not, and the
  box is skipped entirely when stdout is not a terminal, so piping still yields
  plain text.
- The folder picker navigates with arrow keys and marks folders that are
  already brains. "Use this folder" and "create a new folder here" are rows of
  the list rather than the hidden keys `a`/`n`, so enter always does the
  obvious thing for the highlighted row and the right arrow is what descends.
  The original numbered/typed browser remains as a fallback for terminals that
  cannot enter raw mode.
- Choosing a folder scaffolds it there and then, so the app opens on "add a
  data source" rather than on a "scaffold the project" checkbox.
- Data sources are picked by type — `local folder`, `preset`, `http api`,
  `url`, `custom` — each badged with whether it leaves you a working connector
  or one that still needs code. Adding a local folder browses with the folder
  picker instead of asking for a pasted path.
- Adding a URL when graphify is missing offers to install it instead of
  sending you back to step 1 with nothing to do from where you are.
- `brain guide` step 4 distinguishes a connector that still needs code from one
  whose account details were never filled in, and names the missing constant.

- `cli.py` is argparse plumbing only; project operations moved to `project.py`,
  the playbook to `steps.py`, the flow to `app.py`, the interactive operations
  a step performs to `actions.py` and the prompt helpers to `prompt.py`, so the
  guided path and the individual commands share one implementation.
- The full `graphify extract` pass passes `--no-gitignore`. graphify honors
  `.gitignore`, which lists `raw/`, so without it a brain's entire corpus was
  skipped.

### Fixed

- A mirrored folder now brings in the files graphify cannot read. graphify
  indexes Markdown, text, PDF, Office documents and images, and silently skips
  everything else — so a folder of CSV or JSON exports was copied into the
  brain and ignored, leaving `brain sync` reporting success over a nearly empty
  graph. Tables and JSON become the same Markdown records every other connector
  writes, one per row, and anything still unreadable is named in the summary
  instead of disappearing. On a six-file test folder: 2 files indexed before, 7
  after.
- Paragraphs inside the app's box wrapped two columns too wide and broke into
  ragged lines. `FRAME_CHROME` counted the border but not the panel's padding.
- A scheduled sync now runs `brain sync --full`. Without it the LaunchAgent
  rebuilt with `graphify update`, a local AST pass that never reads documents:
  new files were mirrored into `raw/` and never entered the graph, while every
  run reported success. A brain left to sync on its own quietly stopped being
  current — the exact failure scheduling exists to prevent. `graphify extract`
  is gated by its own manifest and semantic cache, so an unchanged corpus
  re-extracts nothing and costs no tokens.
- A connector that cannot find its credential says so once, with the command to
  fix it, instead of repeating a truncated copy of the message for every object
  it was going to collect.
- `brain secret set` no longer passes the credential to `security` as a
  command-line argument. Process arguments are readable by anything running as
  the same user for the lifetime of the command, and also reach shell history
  and launchd logs — the leak this project's own rules forbid. The value now
  goes to `security`'s stdin. Storing is additionally confirmed by reading the
  value back, because `security` exits 0 on paths that store nothing or store
  something else, and a credential falsely reported as saved surfaces much
  later as an authentication failure in a connector.
- A scheduled sync could not find `graphify` or `claude`. launchd gives a job
  only `/usr/bin:/bin:/usr/sbin:/sbin`, so `brain schedule` now resolves those
  directories while it still has the user's environment and pins them into the
  LaunchAgent. Without `claude` on that PATH, an unattended `--full` sync lost
  its LLM backend.
- An unreadable `connectors/state/<name>.json` no longer aborts the run. A
  naive (timezone-less) timestamp raised `TypeError` out of `is_due()` and took
  down the whole sync before any connector had run; anything unparseable there
  now simply means the connector is due.
- A `registry.yaml` entry with no `name` is reported and skipped instead of
  raising `KeyError` through `brain sync` and `brain status`.
- `brain new-connector` and `brain status` disagreed about whether a connector
  was finished. Two regexes detected unfilled `REPLACE_ME` constants and only
  one of them accepted a digit in the suffix, so a connector could be announced
  as ready while step 4 refused to tick for it.
- Installation no longer uses `pip install --user`, which modern Homebrew and
  system Pythons refuse outright (PEP 668, `externally-managed-environment`).
  The virtualenv also makes "`brain` and `graphify` under one interpreter"
  structural rather than something to check after the fact.
- A rebuild that fails for lack of an LLM backend now explains the ways out
  (export a key, or run `/graphify` inside Claude Code) instead of exiting
  non-zero with no explanation.
- "Change project" run from inside a brain opened the picker instead of
  answering with the brain you were already standing in, which made the tool a
  silent no-op.
- Backing out of the opening folder picker exits 0 with a note, not 1.
- `--probe` commands name the interpreter `brain` is installed under. The
  generated shebang is `env python3`, which on a machine with several Python 3
  installs need not be the one `brainiphy_cli` lives under — the connector then
  died on its own import before reaching the API. `brain sync` had always run
  them this way; the printed hints and the app now agree with it.

## [0.1.0] - 2026-07-27

Initial release: the `brain` CLI (`init`, `new-connector`, `sync`,
`connect-claude`, `schedule`, `secret`, `status`), the connector contract and
templates, Keychain-backed credentials, LaunchAgent scheduling, the interactive
folder picker, the Rich-styled output layer, and the `SKILL.md` playbook.
