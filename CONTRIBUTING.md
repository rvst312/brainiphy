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
- **scope** — the module or area: `cli`, `sync`, `app`, `actions`, `steps`,
  `project`, `prompt`, `picker`, `ui`, `keychain`, `skill`, `deps`, `tests`
  (optional, but use it)
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

Two layers, both run by CI on Linux and macOS across the supported Python
versions. Run both before opening a PR.

```sh
python -m unittest discover -s tests        # unit suite, no graphify, no network
./scripts/smoke.sh                          # end-to-end, the real CLI
```

The unit suite is plain `unittest` — no dependency to install, and it runs on
the same interpreter as the package. It covers the decisions that fail
*silently*: which LLM backend is chosen, which graphify command rebuilds the
graph, whether a connector is due, the slug and escaping that make a re-sync
idempotent, what the checklist reads off disk, and the retry/NoScope behavior
of the HTTP layer. Add to it whenever you fix something that was invisible from
the outside — three of the bugs it now guards were found by writing it.

The smoke test covers the shape the unit suite cannot: the real `brain` binary,
the real templates, a generated connector actually running. Add an assertion to
it whenever you fix something it would have caught.

`brain new` is exercised by neither — it refuses without a TTY, and piping
answers through `script -q /dev/null` does not work either (the pty eats
stdin). Drive `app.run()` from a Python snippet that replaces `keys.supported`,
`picker.is_interactive`, `keys.read_key` and the `prompt.*` functions with
scripted answers; that covers the whole flow including the real subprocess
calls. Count the keypresses carefully — `_pause()` after a framed action reads
one, and so does `_prepare()` on a folder that was not scaffolded yet. An
off-by-one there looks exactly like a bug in the code under test.

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
  `sync.py`, `app.py` or `actions.py`, so the guided flow and the individual
  commands cannot drift apart.
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
