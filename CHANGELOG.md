# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  already brains. The original numbered/typed browser remains as a fallback for
  terminals that cannot enter raw mode.
- `brain guide` step 4 distinguishes a connector that still needs code from one
  whose account details were never filled in, and names the missing constant.

- `cli.py` is argparse plumbing only; project operations moved to `project.py`,
  the playbook to `steps.py`, the flow to `app.py`, the interactive operations
  a step performs to `actions.py` and the prompt helpers to `prompt.py`, so the
  guided path and the individual commands share one implementation.
- Every graphify invocation passes `--no-gitignore`. graphify honors
  `.gitignore`, which lists `raw/`, so without it a brain's entire corpus was
  skipped.

### Fixed

- A rebuild that fails for lack of an LLM backend now explains the two ways out
  (export a key, or run `/graphify` inside Claude Code) instead of exiting
  non-zero with no explanation.

## [0.1.0] - 2026-07-27

Initial release: the `brain` CLI (`init`, `new-connector`, `sync`,
`connect-claude`, `schedule`, `secret`, `status`), the connector contract and
templates, Keychain-backed credentials, LaunchAgent scheduling, the interactive
folder picker, the Rich-styled output layer, and the `SKILL.md` playbook.
