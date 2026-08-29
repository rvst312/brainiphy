#!/usr/bin/env bash
#
# End-to-end smoke test for the `brain` CLI — everything that can run without
# graphify, an LLM API key or a TTY. Run it before opening a PR; CI runs the
# same script on every push.
#
# What it deliberately does NOT cover: `brain sync` without --dry-run (it ends
# in a graphify call), `connect-claude` and `schedule` (they write to the
# user's home directory), and `brain new` beyond checking that it refuses to
# run headless — driving the app needs the scripted-keypress harness described
# in CONTRIBUTING.md. The unit suite (python -m unittest discover -s tests)
# covers the decisions underneath these commands.
set -euo pipefail

brain=${BRAIN:-brain}
# A generated connector imports brainiphy_cli, so it has to run under the
# interpreter `brain` itself was installed with — `python3` on this machine may
# well be a different one, and the script would die on its own import.
brain_python=$(head -1 "$(command -v "$brain")" | sed 's|^#!||')
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Creating a brain registers it in the user's list. Point that list into the
# scratch directory so a smoke run never appends temp folders to it.
export BRAINIPHY_HOME="$work/config"

source_dir="$work/source"
project="$work/project"
mkdir -p "$source_dir"
printf '# Note\n\nA document the brain should end up mirroring.\n' > "$source_dir/note.md"
# A real folder is not all Markdown. graphify reads neither of these, so the
# connector has to turn one into records and name the other.
printf 'Cliente,Importe\nacme,1200\nmoda lunar,890\n' > "$source_dir/facturacion.csv"
printf 'not something graphify can read\n' > "$source_dir/hoja.numbers"

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
"$brain_python" "$project/connectors/docs/sync.py" --out "$project/raw/docs"
test -f "$project/raw/docs/note.md" || { echo "mirror did not copy the source document" >&2; exit 1; }

step "the mirror converts what graphify cannot read"
# A CSV copied verbatim is a file sitting in the brain that will never be in
# its graph, and nothing else would tell you that.
test -d "$project/raw/docs/_converted/facturacion" \
  || { echo "the CSV was not converted into records" >&2; exit 1; }
test "$(find "$project/raw/docs/_converted/facturacion" -name '*.md' | wc -l)" -eq 2 \
  || { echo "expected one record per CSV row" >&2; exit 1; }
grep -q 'cliente: "acme"' "$project"/raw/docs/_converted/facturacion/*.md \
  || { echo "the CSV columns did not become frontmatter" >&2; exit 1; }

# Re-running must not duplicate them, and rsync --delete must not remove them:
# they have no counterpart in the source folder, which is what --delete eats.
second_run=$("$brain_python" "$project/connectors/docs/sync.py" --out "$project/raw/docs")
test "$(find "$project/raw/docs/_converted/facturacion" -name '*.md' | wc -l)" -eq 2 \
  || { echo "a second run changed the converted records" >&2; exit 1; }
case "$second_run" in *hoja.numbers*) ;; *)
  echo "a file that cannot be indexed was not reported" >&2; exit 1;; esac

step "brain new-connector (generic template)"
"$brain" new-connector "$project" crm
grep -q 'fetch_records' "$project/connectors/crm/sync.py" || { echo "template did not land" >&2; exit 1; }

step "brain list / forget / add"
# Captured rather than piped into grep: under `set -o pipefail`, grep -q closes
# the pipe on its first match, brain exits on the broken pipe, and the whole
# pipeline reports a failure that never happened.
listed() { "$brain" list; }
case "$(listed)" in *"$(basename "$project")"*) ;; *)
  echo "a scaffolded brain did not appear in the list" >&2; exit 1;; esac

"$brain" forget "$project" > /dev/null
case "$(listed)" in *"$(basename "$project")"*)
  echo "forget did not remove it from the list" >&2; exit 1;; esac
# The promise `brain forget` makes: the entry goes, the brain does not.
test -f "$project/connectors/registry.yaml" || { echo "forget deleted the brain's files" >&2; exit 1; }
test -x "$project/connectors/docs/sync.py" || { echo "forget deleted a connector" >&2; exit 1; }

"$brain" add "$project" > /dev/null
case "$(listed)" in *"$(basename "$project")"*) ;; *)
  echo "add did not put it back" >&2; exit 1;; esac

step "brain view without a graph explains itself"
"$brain" view "$project" > /dev/null 2>&1 && { echo "view should fail when there is no graph" >&2; exit 1; }

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
