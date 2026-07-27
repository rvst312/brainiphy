<!--
Title must follow Conventional Commits — it becomes the commit subject on main
after the squash merge:  <type>(<scope>): <subject>
-->

## What

<!-- One or two sentences. The reviewer reads this before the diff. -->

## Why

<!-- The problem, or the constraint that forced this shape. If you discovered
     something about graphify or launchd the hard way, say it here — that is
     the part nobody can reconstruct from the diff. -->

## How it was tested

<!-- Commands you actually ran, against what. `./scripts/smoke.sh` at minimum
     for anything touching the CLI. -->

## Checklist

- [ ] `./scripts/smoke.sh` passes locally
- [ ] Each commit leaves the package importable and the CLI runnable
- [ ] `SKILL.md` and `steps.py` updated together, if the process a user follows changed
- [ ] `CLAUDE.md` updated, if the architecture or a non-obvious constraint changed
- [ ] `CHANGELOG.md` updated under `Unreleased`, if this is user-visible
- [ ] No new lint/format tooling, no Spanish in user-facing strings, no secrets through arguments or files
