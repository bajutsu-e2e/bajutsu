#!/usr/bin/env bash
# Refuse to run when a per-worktree git setting has been written to the shared config (issue #1803).
#
# With `extensions.worktreeConfig` enabled, git drops its built-in exception for `core.bare` and
# `core.worktree` — the one that otherwise confines a shared value to the main working tree. A
# shared value then applies to *every* worktree, and git-worktree(1) says so plainly: `core.worktree`
# "should never be shared", and `core.bare` "should not be shared if the value is core.bare=true".
#
# The failure that follows is silent, not loud, which is why this guard exists rather than a note in
# the docs. A shared `core.worktree` makes every command in every worktree resolve to one other
# tree's directory while `--git-dir` still answers locally: `git status` lists another branch's
# files, `git add` reports success and changes nothing, `git commit --amend` drops a file from the
# commit, and `git checkout -- <path>` writes into a concurrent session's worktree. A shared
# `core.bare = true` is the loud half — every invocation fails with "this operation must be run in a
# work tree" — but it hides behind the first until that one is cleared.
#
# This only reports. The two settings arrive from outside this repository's tooling (nothing here
# writes either one), and the correct repair differs depending on whether the checkout is the main
# or a linked worktree, so the remedy is printed for a human to apply rather than guessed at.
#
# Run by `make hooks`, which `check`, `setup`, and `worktree` all reach.
set -euo pipefail

cd "$(dirname "$0")/.."

# git's location variables override discovery from `cwd`, and a hook exports them into everything it
# runs — absolute in a linked worktree. Left set, every read below would answer for the pushing
# checkout instead of this one, which is the class of bug this script is here to catch.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY

# Then set one of them back, deliberately. Every git command — `git config --file` no less than
# `git rev-parse` — resolves `core.worktree` during repository setup, before it does the work asked
# of it. So a stale value, the worst case here because it points at a worktree since removed, makes
# even a plain config read die with "Invalid path", and a guard that took that for "not a checkout"
# would fall silent on exactly the repository it exists to catch. `GIT_WORK_TREE` overrides both
# offending settings, and `$PWD` is the right value by construction: this script lives in the
# checkout it judges, and the `cd` above put us at its root.
export GIT_WORK_TREE="$PWD"

if ! command -v git >/dev/null 2>&1; then
  # A source export with no git at all: nothing to read, and nothing wrong.
  exit 0
fi

if ! common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>&1)"; then
  # An export or a tarball has no config to be wrong about, and the gate's other steps report a
  # missing checkout far better than a config guard can. Any *other* failure — a `safe.directory`
  # refusal, a corrupt repository — says nothing about these two settings, so it must not be
  # mistaken for a clean bill of health.
  case "$common_dir" in
    *"not a git repository"*) exit 0 ;;
    *)
      echo "check-worktree-config: could not read this repository's git configuration:" >&2
      echo "  $common_dir" >&2
      exit 1
      ;;
  esac
fi

shared_config="$common_dir/config"
[ -f "$shared_config" ] || exit 0

# `--file` rather than a plain `git config` read: only the *shared* file is in question, and a plain
# read would fold in the per-worktree config (where these two settings are correct), plus the global
# and system files, and so report a problem that is not there.
shared_get() {
  git config --file "$shared_config" --get "$1" 2>/dev/null || true
}

[ "$(git config --file "$shared_config" --type=bool --get extensions.worktreeConfig 2>/dev/null || true)" = "true" ] || exit 0

offenders=()
worktree_value="$(shared_get core.worktree)"
if [ -n "$worktree_value" ]; then
  offenders+=("core.worktree = $worktree_value")
fi
# Only `true` offends: git-worktree(1) singles out that value, and a shared `core.bare = false` is
# both harmless and what a normal clone carries.
if [ "$(git config --file "$shared_config" --type=bool --get core.bare 2>/dev/null || true)" = "true" ]; then
  offenders+=("core.bare = true")
fi

if [ "${#offenders[@]}" -eq 0 ]; then
  exit 0
fi

{
  echo "check-worktree-config: a per-worktree git setting is in the SHARED config, so it applies to"
  echo "check-worktree-config: every worktree of this repository (extensions.worktreeConfig is on):"
  echo
  for offender in "${offenders[@]}"; do
    echo "    $offender"
  done
  echo
  echo "  in: $shared_config"
  echo
  echo "  Left in place, git silently reads a different working tree than your shell's directory:"
  echo "  'git status' lists another branch's files, 'git add' succeeds without changing anything,"
  echo "  'git commit --amend' drops files, and 'git checkout -- <path>' writes into another"
  echo "  session's worktree. See issue #1803."
  echo
  echo "  Fix it from the affected worktree, then re-run:"
  if [ -n "$worktree_value" ]; then
    echo "      git config --unset core.worktree      # remove it from the shared config"
  fi
  echo "      git config --worktree core.bare false  # per-worktree, where it belongs"
  echo
  echo "  A worktree that genuinely needs either setting keeps it in its own config, via"
  echo "  'git config --worktree'. See docs/ai-development.md#isolate-concurrent-sessions-with-worktrees."
} >&2

exit 1
