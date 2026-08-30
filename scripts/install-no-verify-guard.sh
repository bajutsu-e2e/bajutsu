#!/usr/bin/env bash
# Optional, per-developer installer for a personal `git()` shell function that refuses
# `git push --no-verify` inside any repository whose toplevel carries
# `.githooks/no-verify-guard-marker` (this repo included).
#
# Why a shell function, and why it lives outside `make setup` / `make hooks`: `--no-verify` skips
# every git hook unconditionally, and git refuses to let a config alias override an existing
# subcommand name (`git help config`: "aliases that hide existing Git commands are ignored" —
# verified by hand against this repo's own `push`). The only point left that ever sees the raw
# `--no-verify` flag before git acts on it is command-name resolution itself, which only a real
# `git` wrapper controls — and that means editing a file outside this repository (the caller's
# shell rc). `make setup` and `make hooks` only ever touch files inside the clone, so this
# installer stays a separate, explicit step a developer opts into by running it.
#
# This is a personal safeguard, not a repository-wide guarantee: removing this block, or calling
# `command git push --no-verify` (or the git binary's full path) directly, still gets through. The
# actual, unavoidable backstop is CI's independent `make check` re-run before merge
# (docs/ai-development.md#never-push-red) — this installer only saves the round trip to that gate.
set -euo pipefail

MARKER_BEGIN="# >>> bajutsu no-verify guard >>>"
MARKER_END="# <<< bajutsu no-verify guard <<<"

rc_file="${BAJUTSU_GUARD_RC_FILE:-}"
if [ -z "$rc_file" ]; then
  case "${SHELL:-}" in
    */zsh) rc_file="$HOME/.zshrc" ;;
    */bash) rc_file="$HOME/.bashrc" ;;
    *)
      echo "install-no-verify-guard: unrecognized \$SHELL ('${SHELL:-unset}')." >&2
      echo "install-no-verify-guard: set BAJUTSU_GUARD_RC_FILE=<path to your shell rc> and retry." >&2
      exit 1
      ;;
  esac
fi

if [ -f "$rc_file" ] && grep -qF "$MARKER_BEGIN" "$rc_file"; then
  echo "install-no-verify-guard: already installed in $rc_file — nothing to do."
  exit 0
fi

{
  echo ""
  echo "$MARKER_BEGIN"
  cat <<'SNIPPET'
# Refuses `git push --no-verify` inside any repository whose toplevel carries a
# `.githooks/no-verify-guard-marker` file. Installed by scripts/install-no-verify-guard.sh
# (bajutsu); see docs/ai-development.md#never-push-red. Safe to delete this whole block.
git() {
  if [ "$1" = "push" ]; then
    __bajutsu_guard_top="$(command git rev-parse --show-toplevel 2>/dev/null)" || __bajutsu_guard_top=""
    if [ -n "$__bajutsu_guard_top" ] && [ -f "$__bajutsu_guard_top/.githooks/no-verify-guard-marker" ]; then
      for __bajutsu_guard_arg in "$@"; do
        if [ "$__bajutsu_guard_arg" = "--no-verify" ]; then
          echo "error: git push --no-verify is forbidden in this repository — see docs/ai-development.md#never-push-red" >&2
          unset __bajutsu_guard_top __bajutsu_guard_arg
          return 1
        fi
      done
    fi
    unset __bajutsu_guard_top
  fi
  command git "$@"
}
SNIPPET
  echo "$MARKER_END"
} >> "$rc_file"

echo "install-no-verify-guard: added to $rc_file — restart your shell (or 'source $rc_file') to activate."
