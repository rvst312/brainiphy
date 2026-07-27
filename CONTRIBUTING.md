# Contributing to brainiphy

This repo is two things at once: the source of a Claude Code skill (`SKILL.md`)
and the Python package that skill drives (`src/brainiphy_cli/`). It is normally
symlinked at `~/.claude/skills/brainiphy`, so **whatever branch is checked out
here is the skill every Claude session sees**. Keep that in mind before leaving
a half-finished branch checked out — or use a `git worktree` for side work
(see [Worktrees](#worktrees)).

Read `SKILL.md` first: it is the primary spec for how the CLI is meant to
behave. `CLAUDE.md` covers the architecture. This file covers the workflow.

## Setting up

```sh
/usr/local/opt/python@3.11/bin/python3.11 -m pip install --user -e ~/.claude/skills/brainiphy
```

Editable install — needed once so the `brain` binary exists on PATH. After that
edits to `src/brainiphy_cli/*.py` take effect immediately, with two exceptions:

- Re-run the install after touching `dependencies` in `pyproject.toml`. An
  editable install does not pick up new dependencies on its own, and a missing
  `rich` breaks every command (`ui.py` imports it at module level).
- `brain` and `graphify` must resolve to the *same* interpreter — this machine
  has several Python 3 installs, and `brain sync` finds graphify by PATH
  lookup. Check with `head -1 $(which graphify)`.

## Branches

Trunk-based: `main` is always in a state you could install and use. Everything
else happens on a short-lived branch that ends in a pull request.

```
<type>/<short-kebab-summary>        feat/folder-mirrors, fix/sync-empty-registry, docs/skill-steps
```

Types are the same set as the commit types below. Branch off the latest `main`,
keep it to days rather than weeks, rebase (don't merge) `main` into it when it
falls behind, and delete it after the PR lands.

```sh
git switch main && git pull
git switch -c feat/my-thing
# …
git fetch origin && git rebase origin/main      # when main has moved
```

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), because the
subject line is the only part anyone reads in a year:

```
<type>(<scope>): <subject>
```

- **type** — `feat`, `fix`, `docs`, `refactor`, `perf`, `chore`, `ci`, `revert`
- **scope** — the module or area: `cli`, `sync`, `wizard`, `steps`, `project`,
  `prompt`, `picker`, `ui`, `keychain`, `skill`, `deps` (optional, but use it)
- **subject** — imperative, lower case, no trailing period, ≤72 characters
- **breaking change** — `!` after the scope *and* a `BREAKING CHANGE:` footer

The body is for *why*: what the change replaces, what constraint forced it,
what would break if someone undid it. The diff already says what changed.
Several of the constraints this tool works around (graphify not following
symlinks, `.gitignore` hiding `raw/`, `graphify update` being a no-op on
documents) were only discovered empirically — when you hit one, put it in the
commit body and in `CLAUDE.md`.

Turn on the template once and the reminders show up in your editor:

```sh
git config commit.template .gitmessage
```

Keep commits atomic: each one should leave the package importable and the CLI
runnable. Splitting a large change into "extract the module" then "use it" is
encouraged; splitting it into commits that do not run is not.

## Pull requests

1. Push the branch and open a PR against `main` — early and in draft is fine.
2. CI must be green (see below). Fill in the template: what, why, how you
   tested it.
3. Merge, then delete the branch. Which button depends on what the branch
   looks like:
   - **Rebase merge** when every commit on the branch already stands on its
     own — atomic, conventional subject, package importable at each one. The
     history is worth keeping, and `main` stays linear either way.
   - **Squash merge** otherwise — a branch of "wip", "fix review comment" and
     "actually fix it" becomes one commit whose subject is the PR title. That
     is why CI checks the title.

   Never a merge commit; `main` is a straight line.

If a change touches the process a user follows, `SKILL.md` and `steps.py`
change **in the same PR** — they are two renderings of the same seven steps and
they drift silently if separated.

## Testing

There is no unit test suite yet. What exists is an end-to-end smoke test of
everything that runs without graphify, an API key or a TTY:

```sh
./scripts/smoke.sh
```

CI runs the same script on Linux and macOS across the supported Python
versions. Run it before opening a PR; add an assertion to it whenever you fix
something it would have caught.

`brain new` can't be exercised that way — it refuses without a TTY, and piping
answers through `script -q /dev/null` does not work either (the pty eats
stdin). Drive `wizard.run()` from a Python snippet that replaces
`picker.is_interactive` and the four `prompt.*` functions with scripted
answers; that covers the whole flow including the real subprocess calls.

For anything touching a target project, work against a scratch directory:

```sh
brain init /tmp/test-brain
brain new-connector /tmp/test-brain docs --mirror /tmp/some-folder
brain sync /tmp/test-brain --dry-run
brain guide /tmp/test-brain
```

## House rules

These are the ones that are easy to violate without noticing:

- **No new tooling without a reason.** There is no linter or formatter config
  on purpose. Match the existing style — plain `argparse`, dataclasses,
  `from __future__ import annotations`.
- **English only** in user-facing strings, comments and docs. The repo was
  translated from Spanish; don't reintroduce it.
- **Rich `Text`, never markup strings**, and `highlight=False`. Connector names
  and paths come from user-written files, and a stray `[` would be parsed as a
  markup tag. Use `ui.cell()` in tables for the same reason.
- **Secrets only through the Keychain.** `keychain.get_secret()` is the only
  path. Never let a secret value flow through a CLI argument, a file brainiphy
  writes, or `registry.yaml` — shell history, process listings and launchd logs
  all leak.
- **`cli.py` stays argparse plumbing.** The work belongs in `project.py`,
  `sync.py` or `wizard.py`, so the guided flow and the individual commands
  cannot drift apart.
- **`steps.inspect()` stays cheap and read-only.** It runs on every
  `brain status`; no subprocess calls in it.

## Worktrees

Because the checkout doubles as the installed skill, the least disruptive way
to work on two branches at once is a second worktree rather than a branch
switch:

```sh
git worktree add ../brainiphy-fix fix/my-thing
# …
git worktree remove ../brainiphy-fix
```

The primary checkout — and therefore the live skill — stays on the branch you
left it on.

## Releasing

Versions follow [SemVer](https://semver.org/) and live in `pyproject.toml`.
To cut one: move the `Unreleased` entries in `CHANGELOG.md` under a new version
heading, bump `pyproject.toml` in the same commit
(`chore(release): 0.2.0`), then tag `main`:

```sh
git tag -a v0.2.0 -m "brainiphy 0.2.0"
git push origin v0.2.0
```
