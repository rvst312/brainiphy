# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `brain new` — guided, end-to-end setup that walks the seven steps of building
  a brain, asking questions and generating what it can.
- `brain guide` — shows those seven steps and how far along a given project is;
  the same data drives the next-step line in `brain status`.
- `brain new-connector --mirror <folder>` — a complete, ready-to-run connector
  that mirrors a local folder into the brain, with no `fetch_records()` to
  write.
- `brain sync --full` — full `graphify extract` re-index instead of the
  incremental, code-only `graphify update`. Happens automatically on the first
  build.
- Contributor workflow: `CONTRIBUTING.md`, commit template, PR and issue
  templates, `scripts/smoke.sh`, and CI running it on Linux and macOS across
  Python 3.9–3.13.

### Changed

- `cli.py` is argparse plumbing only; project operations moved to `project.py`,
  the playbook to `steps.py`, the guided flow to `wizard.py` and the prompt
  helpers to `prompt.py`, so the guided path and the individual commands share
  one implementation.
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
