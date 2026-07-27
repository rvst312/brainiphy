#!/usr/bin/env bash
#
# End-to-end smoke test for the `brain` CLI — everything that can run without
# graphify, an LLM API key or a TTY. Run it before opening a PR; CI runs the
# same script on every push.
#
# What it deliberately does NOT cover: `brain sync` without --dry-run (it ends
# in a graphify call), `connect-claude` and `schedule` (they write to the
# user's home directory), and `brain new` beyond checking that it refuses to
# run headless — driving the wizard needs the scripted-prompt harness described
# in CONTRIBUTING.md.
set -euo pipefail

brain=${BRAIN:-brain}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

source_dir="$work/source"
project="$work/project"
mkdir -p "$source_dir"
printf '# Note\n\nA document the brain should end up mirroring.\n' > "$source_dir/note.md"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "brain init"
"$brain" init "$project"
test -f "$project/connectors/registry.yaml" || { echo "registry.yaml missing" >&2; exit 1; }
# Without this entry graphify's AST extractor indexes the connector scripts
# themselves as source code.
grep -q '^connectors/$' "$project/.graphifyignore" || { echo ".graphifyignore does not cover connectors/" >&2; exit 1; }
grep -q '^raw/$' "$project/.gitignore" || { echo ".gitignore does not cover raw/" >&2; exit 1; }

step "brain new-connector --mirror"
"$brain" new-connector "$project" docs --mirror "$source_dir" --interval-minutes 5
test -x "$project/connectors/docs/sync.py" || { echo "connector script missing or not executable" >&2; exit 1; }
grep -q 'name: docs' "$project/connectors/registry.yaml" || { echo "connector not registered" >&2; exit 1; }

step "generated connector runs and mirrors the folder"
python3 "$project/connectors/docs/sync.py" --out "$project/raw/docs"
test -f "$project/raw/docs/note.md" || { echo "mirror did not copy the source document" >&2; exit 1; }

step "brain new-connector (generic template)"
"$brain" new-connector "$project" crm
grep -q 'fetch_records' "$project/connectors/crm/sync.py" || { echo "template did not land" >&2; exit 1; }

step "brain sync --dry-run"
"$brain" sync "$project" --dry-run

step "brain guide / brain status"
"$brain" guide "$project"
"$brain" guide "$project" --verbose
"$brain" status "$project"

step "brain new refuses to run without a TTY"
if "$brain" new "$project" < /dev/null > /dev/null 2>&1; then
  echo "brain new should refuse to run headless — agents and scripts must use the individual commands" >&2
  exit 1
fi

printf '\n\033[32mall good\033[0m\n'
