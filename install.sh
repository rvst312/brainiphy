#!/usr/bin/env bash
#
# brainiphy installer.
#
#   curl -fsSL https://raw.githubusercontent.com/rvst312/brainiphy/main/install.sh | bash
#
# Installs into a dedicated virtualenv and symlinks the two entry points into
# ~/.local/bin. Two reasons, both learned the hard way:
#
#   - `pip install --user` is refused outright on a Homebrew or system Python
#     (PEP 668, "externally-managed-environment"), which is most Macs now.
#   - `brain` shells out to `graphify`, so they must run under the *same*
#     interpreter. With several Pythons installed, "pip3 install" twice does
#     not guarantee that. One venv holding both makes it structural rather
#     than something to verify and hope for.
#
# Deliberately non-interactive: piped from curl, stdin is the script itself, so
# there is nothing to read answers from. Everything it might ask is a flag, and
# anything it changes outside its own directory (your shell profile, the Claude
# skills folder) is announced and undone by `--uninstall`.
#
# Flags:
#   --prefix DIR     where to put the checkout (default: ~/.claude/skills/brainiphy)
#   --python BIN     interpreter to build the venv with (default: newest python3 >= 3.9)
#   --no-graphify    skip the graphify engine
#   --no-path        do not touch the shell profile
#   --no-skill       do not register it as a Claude Code skill
#   --uninstall      remove the venv, the symlinks, the PATH block and the skill link
#   --dry-run        print what would happen, change nothing
#   --help
#
# Env: BRAINIPHY_REPO overrides the git URL (forks, and the test harness).
set -euo pipefail

REPO_URL="${BRAINIPHY_REPO:-https://github.com/rvst312/brainiphy.git}"
SKILL_DIR="$HOME/.claude/skills/brainiphy"
VENV_DIR="$HOME/.local/share/brainiphy/venv"
BIN_DIR="$HOME/.local/bin"
ENTRY_POINTS="brain graphify"

PREFIX=""
PYTHON=""
DO_GRAPHIFY=1
DO_PATH=1
DO_SKILL=1
UNINSTALL=0
DRY_RUN=0

MIN_MINOR=9          # pyproject says requires-python >= 3.9
MARKER_OPEN="# >>> brainiphy >>>"
MARKER_CLOSE="# <<< brainiphy <<<"

# ------------------------------------------------------------------ output --

if [ -t 1 ]; then
  BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m'); RED=$(printf '\033[31m')
  GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); OFF=$(printf '\033[0m')
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; OFF=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$BOLD" "$OFF" "$BOLD" "$1" "$OFF"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
info() { printf '  %s·%s %s%s%s\n' "$DIM" "$OFF" "$DIM" "$1" "$OFF"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
die()  { printf '\n  %s✗%s %s\n\n' "$RED" "$OFF" "$1" >&2; exit 1; }

# Runs a command, or prints it under --dry-run. Failure is fatal: the previous
# version let a failed pip carry on and only noticed at the verify step, which
# buried the real error under a pile of later output.
run() {
  if [ "$DRY_RUN" = 1 ]; then
    printf '  %swould run:%s %s\n' "$DIM" "$OFF" "$*"
    return 0
  fi
  "$@" || die "failed: $*"
}

usage() { sed -n '3,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

# ------------------------------------------------------------------- args --

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)      PREFIX="${2:?--prefix needs a directory}"; shift 2 ;;
    --python)      PYTHON="${2:?--python needs an interpreter}"; shift 2 ;;
    --no-graphify) DO_GRAPHIFY=0; shift ;;
    --no-path)     DO_PATH=0; shift ;;
    --no-skill)    DO_SKILL=0; shift ;;
    --uninstall)   UNINSTALL=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage ;;
    *)             die "unknown option: $1  (try --help)" ;;
  esac
done

[ "$DO_GRAPHIFY" = 1 ] || ENTRY_POINTS="brain"

# Without the skill, the checkout has no reason to live in the skills folder.
if [ -z "$PREFIX" ]; then
  if [ "$DO_SKILL" = 1 ]; then PREFIX="$SKILL_DIR"; else PREFIX="$HOME/.local/share/brainiphy/src"; fi
fi

# ---------------------------------------------------------------- python --

python_ok() {
  # Usable means: runs, new enough, and can build a venv. A python without the
  # venv module (some distro splits it into a separate package) fails later in
  # a much more confusing way than it does here.
  command -v "$1" >/dev/null 2>&1 || return 1
  "$1" -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, $MIN_MINOR) else 1)" 2>/dev/null || return 1
  "$1" -c "import venv, ensurepip" 2>/dev/null
}

find_python() {
  if [ -n "$PYTHON" ]; then
    python_ok "$PYTHON" || die "$PYTHON is unusable (needs Python >= 3.$MIN_MINOR with the venv module)"
    command -v "$PYTHON"; return
  fi
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if python_ok "$candidate"; then command -v "$candidate"; return; fi
  done
  die "no Python >= 3.$MIN_MINOR with the venv module found. Install one, or pass --python /path/to/python3"
}

# --------------------------------------------------------------- profile --

profile_file() {
  case "$(basename "${SHELL:-}")" in
    zsh)  printf '%s' "$HOME/.zshrc" ;;
    bash) if [ -f "$HOME/.bash_profile" ]; then printf '%s' "$HOME/.bash_profile"
          else printf '%s' "$HOME/.bashrc"; fi ;;
    *)    printf '' ;;   # fish and friends use different syntax; tell the user instead
  esac
}

add_to_path() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) ok "already on PATH: $BIN_DIR"; return ;;
  esac

  profile="$(profile_file)"
  if [ -z "$profile" ]; then
    warn "unrecognised shell (${SHELL:-unknown}) — add this to your profile yourself:"
    printf '      export PATH="%s:$PATH"\n' "$BIN_DIR"
    return
  fi
  if [ -f "$profile" ] && grep -qF "$MARKER_OPEN" "$profile"; then
    ok "PATH block already in $(basename "$profile")"
    info "open a new shell, or: export PATH=\"$BIN_DIR:\$PATH\""
    return
  fi

  warn "adding $BIN_DIR to PATH in $profile"
  if [ "$DRY_RUN" = 1 ]; then
    info "would append a marked block (removed by --uninstall)"
  else
    {
      printf '\n%s\n' "$MARKER_OPEN"
      printf 'export PATH="%s:$PATH"\n' "$BIN_DIR"
      printf '%s\n' "$MARKER_CLOSE"
    } >> "$profile"
    ok "added to $(basename "$profile")"
  fi
  info "applies to new shells; for this one:"
  printf '      export PATH="%s:$PATH"\n' "$BIN_DIR"
}

remove_from_path() {
  profile="$(profile_file)"
  [ -n "$profile" ] && [ -f "$profile" ] || return 0
  grep -qF "$MARKER_OPEN" "$profile" || { info "no PATH block in $(basename "$profile")"; return 0; }
  if [ "$DRY_RUN" = 1 ]; then info "would remove the PATH block from $profile"; return 0; fi
  # Keep a copy: this is someone's shell profile, not a generated file. It is
  # also the input to the filter below — reading and writing the same file in
  # one pipeline truncates it before awk ever sees it.
  cp "$profile" "$profile.brainiphy-backup"
  # `close` is a builtin in awk, so a variable of that name is a syntax error.
  # It used to be one here, and because the redirect had already truncated the
  # profile, the failure emptied it instead of filtering it.
  if ! awk -v startmark="$MARKER_OPEN" -v endmark="$MARKER_CLOSE" '
    $0 == startmark { skipping = 1 } !skipping { print } $0 == endmark { skipping = 0 }
  ' "$profile.brainiphy-backup" > "$profile.brainiphy-filtered"; then
    rm -f "$profile.brainiphy-filtered"
    die "could not filter $profile — it is untouched, backup at $profile.brainiphy-backup"
  fi
  mv "$profile.brainiphy-filtered" "$profile"
  ok "removed the PATH block (backup: $(basename "$profile").brainiphy-backup)"
}

# ------------------------------------------------------------- uninstall --

do_uninstall() {
  step "Uninstalling brainiphy"

  for entry in brain graphify; do
    link="$BIN_DIR/$entry"
    # Only ours: a symlink pointing into our venv. Never a binary someone else
    # put there.
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$VENV_DIR/bin/$entry" ]; then
      run rm "$link"; ok "removed $link"
    fi
  done

  if [ -d "$VENV_DIR" ]; then
    run rm -rf "$VENV_DIR"; ok "removed the virtualenv"
  else
    info "no virtualenv at $VENV_DIR"
  fi

  if [ -L "$SKILL_DIR" ]; then
    run rm "$SKILL_DIR"; ok "removed the skill link"
  fi

  remove_from_path

  step "Done"
  info "the checkout at $PREFIX was left alone — delete it by hand if you want it gone"
  exit 0
}

# ------------------------------------------------------------------ main --

printf '\n%s🧠 brainiphy installer%s\n' "$BOLD" "$OFF"
[ "$DRY_RUN" = 1 ] && printf '%s   dry run — nothing will be changed%s\n' "$DIM" "$OFF"

[ "$UNINSTALL" = 1 ] && do_uninstall

step "Checking the environment"

case "$(uname -s)" in
  Darwin) ok "macOS" ;;
  *) warn "$(uname -s) is not macOS — credentials use the macOS Keychain and"
     warn "scheduling uses launchd, so both will be unavailable" ;;
esac

command -v git >/dev/null 2>&1 || die "git is required and was not found"
PY="$(find_python)"
ok "python $("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') at $PY"

step "Fetching the source"

if [ -e "$PREFIX" ] || [ -L "$PREFIX" ]; then
  # A developer checkout symlinked into the skills folder is the normal setup
  # for anyone working on brainiphy itself. Never clobber it.
  target="$PREFIX"
  if [ -L "$PREFIX" ]; then
    target="$(cd -P "$PREFIX" 2>/dev/null && pwd)" || die "$PREFIX is a broken symlink"
  fi
  if [ -d "$target/.git" ]; then
    ok "existing checkout: $target"
    # Updating is a convenience; installing is the job. Every reason a pull can
    # fail here is a state a real checkout is legitimately in — detached at a
    # tag, mid-bisect, on a branch with no upstream, offline — and none of them
    # is a reason to refuse to install what is already on disk.
    if [ -n "$(git -C "$target" status --porcelain 2>/dev/null)" ]; then
      warn "uncommitted changes — leaving the checkout exactly as it is"
    elif ! git -C "$target" symbolic-ref -q HEAD >/dev/null 2>&1; then
      warn "detached HEAD — installing this commit, not updating"
    elif ! git -C "$target" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      warn "branch has no upstream — installing as it is, not updating"
    elif [ "$DRY_RUN" = 1 ]; then
      printf '  %swould run:%s git -C %s pull --ff-only\n' "$DIM" "$OFF" "$target"
    elif git -C "$target" pull --ff-only --quiet 2>/dev/null; then
      ok "updated to the latest commit"
    else
      warn "could not fast-forward — installing the checkout as it is"
    fi
    PREFIX="$target"
  else
    die "$PREFIX exists and is not a git checkout — move it, or pass --prefix"
  fi
else
  run mkdir -p "$(dirname "$PREFIX")"
  run git clone --quiet --depth 1 "$REPO_URL" "$PREFIX"
  ok "cloned into $PREFIX"
fi

step "Building the virtualenv"

if [ -x "$VENV_DIR/bin/python" ]; then
  ok "reusing $VENV_DIR"
else
  run mkdir -p "$(dirname "$VENV_DIR")"
  run "$PY" -m venv "$VENV_DIR"
  ok "created $VENV_DIR"
fi
VENV_PY="$VENV_DIR/bin/python"
info "isolated, so PEP 668 does not apply and nothing touches your system Python"

step "Installing the CLI"

# Editable, because this checkout is also the skill: the files Claude reads and
# the code the CLI runs have to be the same ones, so updating is a git pull.
run "$VENV_PY" -m pip install --quiet --upgrade pip
run "$VENV_PY" -m pip install --quiet --editable "$PREFIX"
ok "brainiphy installed (editable from $PREFIX)"

if [ "$DO_GRAPHIFY" = 1 ]; then
  step "Installing graphify"
  run "$VENV_PY" -m pip install --quiet graphifyy
  ok "graphify installed into the same venv"
  info "same interpreter as brain by construction — which is what lets brain call it"
else
  info "skipped graphify (--no-graphify); the graph cannot be built without it"
fi

step "Linking the commands"

run mkdir -p "$BIN_DIR"
for entry in $ENTRY_POINTS; do
  source_bin="$VENV_DIR/bin/$entry"
  link="$BIN_DIR/$entry"
  if [ "$DRY_RUN" != 1 ] && [ ! -x "$source_bin" ]; then
    die "$entry was not installed into the venv"
  fi
  if [ -e "$link" ] || [ -L "$link" ]; then
    if [ -L "$link" ] && [ "$(readlink "$link")" = "$source_bin" ]; then
      ok "$link already points here"
      continue
    fi
    warn "$link exists and is not ours — leaving it, yours wins on PATH"
    info "ours is at $source_bin"
    continue
  fi
  run ln -s "$source_bin" "$link"
  ok "$link -> $source_bin"
done

if [ "$DO_PATH" = 1 ]; then
  step "PATH"
  add_to_path
else
  info "skipped the PATH check (--no-path)"
fi

if [ "$DO_SKILL" = 1 ]; then
  step "Claude Code skill"
  if [ "$PREFIX" = "$SKILL_DIR" ]; then
    ok "the checkout is the skill: $SKILL_DIR"
  elif [ -L "$SKILL_DIR" ] || [ -e "$SKILL_DIR" ]; then
    ok "already registered: $SKILL_DIR"
  else
    run mkdir -p "$(dirname "$SKILL_DIR")"
    run ln -s "$PREFIX" "$SKILL_DIR"
    ok "linked $SKILL_DIR -> $PREFIX"
  fi
  info "Claude Code can now run the brainiphy skill"
fi

step "LLM backend"

# Not a failure and not something to fix on the user's behalf: indexing
# documents needs a model, and one of the two ways out costs nothing. Better
# said now than at their first sync.
FOUND_KEY=""
for var in ANTHROPIC_API_KEY GEMINI_API_KEY GOOGLE_API_KEY OPENAI_API_KEY DEEPSEEK_API_KEY MOONSHOT_API_KEY; do
  eval "value=\${$var:-}"
  if [ -n "$value" ]; then FOUND_KEY="$var"; break; fi
done

if [ -n "$FOUND_KEY" ]; then
  ok "$FOUND_KEY is set — graphify can index documents"
else
  warn "no LLM API key in the environment"
  info "graphify needs one to index documents (a code-only corpus does not). Either:"
  printf '      export ANTHROPIC_API_KEY=…   %s# or GEMINI_API_KEY, OPENAI_API_KEY…%s\n' "$DIM" "$OFF"
  printf '      %sor open the project in Claude Code and run%s /graphify %s— costs nothing extra%s\n' \
    "$DIM" "$OFF" "$DIM" "$OFF"
fi

step "Verifying"

if [ "$DRY_RUN" = 1 ]; then
  info "skipped (dry run)"
else
  "$VENV_DIR/bin/brain" --help >/dev/null 2>&1 || die "brain is installed but will not run"
  ok "brain runs"
  if [ "$DO_GRAPHIFY" = 1 ]; then
    [ -x "$VENV_DIR/bin/graphify" ] || die "graphify is missing from the venv"
    # Structural now rather than hopeful, but assert it anyway: this mismatch
    # is the single most confusing way for brainiphy to fail.
    [ "$(head -1 "$VENV_DIR/bin/brain")" = "$(head -1 "$VENV_DIR/bin/graphify")" ] \
      || die "brain and graphify have different interpreters inside one venv — this should be impossible"
    ok "brain and graphify share an interpreter"
  fi
fi

step "Done"
printf '  Start with %sbrain%s — it walks you through the seven steps.\n' "$BOLD" "$OFF"
printf '  Undo everything: %sbash %s/install.sh --uninstall%s\n\n' "$DIM" "$PREFIX" "$OFF"
