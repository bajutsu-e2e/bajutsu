#!/usr/bin/env bash
# Personal `git()` shell-function installer that refuses `git push --no-verify` inside any
# repository whose toplevel carries `.githooks/no-verify-guard-marker` (this repo included).
# `make setup` runs this automatically, best-effort, on a fresh checkout — a forgotten manual
# step is exactly the accident this exists to prevent — so most contributors never invoke this
# file directly; `make git-guard-install` remains for (re)running it standalone.
#
# Why a shell function at all: `--no-verify` skips every git hook unconditionally, and git
# refuses to let a config alias override an existing subcommand name (`git help config`:
# "aliases that hide existing Git commands are ignored" — verified by hand against this repo's
# own `push`). The only point left that ever sees the raw `--no-verify` flag before git acts on
# it is command-name resolution itself, which only a real `git` wrapper controls — and that means
# editing a file outside this repository (the caller's shell rc), unlike `core.hooksPath` and the
# other settings `make hooks` wires inside the clone.
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
    */bash)
      # macOS starts an interactive bash as a *login* shell, which reads the first of
      # ~/.bash_profile, ~/.bash_login, ~/.profile that exists — never ~/.bashrc. Writing there
      # on Darwin would install a guard that silently never loads. $SHELL is also the *login*
      # shell, not necessarily the one actually running (e.g. zsh started under a bash login
      # shell); BAJUTSU_GUARD_RC_FILE overrides the detection above for that case.
      if [ "$(uname -s)" = "Darwin" ]; then
        if [ -f "$HOME/.bash_profile" ]; then
          rc_file="$HOME/.bash_profile"
        elif [ -f "$HOME/.bash_login" ]; then
          rc_file="$HOME/.bash_login"
        elif [ -f "$HOME/.profile" ]; then
          rc_file="$HOME/.profile"
        else
          rc_file="$HOME/.bash_profile"
        fi
      else
        rc_file="$HOME/.bashrc"
      fi
      ;;
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
  # git accepts its global options before the subcommand (`git -c a=b push …`), so resolve the
  # subcommand instead of trusting $1 — otherwise `git -c a=b push --no-verify` slips past.
  __bajutsu_guard_sub=""
  __bajutsu_guard_skip=0
  for __bajutsu_guard_arg in "$@"; do
    if [ "$__bajutsu_guard_skip" = 1 ]; then
      __bajutsu_guard_skip=0
      continue
    fi
    case "$__bajutsu_guard_arg" in
      -c|-C|--git-dir|--work-tree|--namespace|--exec-path) __bajutsu_guard_skip=1 ;;
      -*) ;;
      *) __bajutsu_guard_sub="$__bajutsu_guard_arg"; break ;;
    esac
  done
  if [ "$__bajutsu_guard_sub" = "push" ]; then
    __bajutsu_guard_top="$(command git rev-parse --show-toplevel 2>/dev/null)" || __bajutsu_guard_top=""
    if [ -n "$__bajutsu_guard_top" ] && [ -f "$__bajutsu_guard_top/.githooks/no-verify-guard-marker" ]; then
      for __bajutsu_guard_arg in "$@"; do
        if [ "$__bajutsu_guard_arg" = "--no-verify" ]; then
          echo "error: git push --no-verify is forbidden in this repository — see docs/ai-development.md#never-push-red" >&2
          unset __bajutsu_guard_top __bajutsu_guard_arg __bajutsu_guard_sub __bajutsu_guard_skip
          return 1
        fi
      done
    fi
    unset __bajutsu_guard_top
  fi
  unset __bajutsu_guard_sub __bajutsu_guard_skip __bajutsu_guard_arg
  command git "$@"
}
SNIPPET
  echo "$MARKER_END"
} >> "$rc_file"

echo "install-no-verify-guard: added to $rc_file — restart your shell (or 'source $rc_file') to activate."
