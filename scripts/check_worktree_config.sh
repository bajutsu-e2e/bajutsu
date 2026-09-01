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
# writes either one), and the correct repair depends on whether the checkout is the main or a linked
# worktree, so the remedy is printed for a human to apply rather than guessed at.
#
# Every uncertain answer here resolves to a *loud* failure. A guard whose whole purpose is to end a
# silent misconfiguration must never report a clean bill of health on a repository it could not
# actually read, so the only quiet exits below are states that are positively known to be fine.
#
# Run by `make hooks`, which `check`, `setup`, and `worktree` all reach.
set -euo pipefail

cd "$(dirname "$0")/.."

# git's location variables override discovery from `cwd`, and a hook exports them into everything it
# runs — absolute in a linked worktree. Left set, every read below would answer for the pushing
# checkout instead of this one, which is the class of bug this script is here to catch.
# `GIT_COMMON_DIR` earns its place twice over: it names the very file this guard reads.
# `GIT_CEILING_DIRECTORIES` is here for the opposite reason — it *bounds* the upward search rather
# than redirecting it, and an inherited bound could stop git finding the checkout that is right here.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CEILING_DIRECTORIES

# Then set one of them back, deliberately. Every git command — `git config --file` no less than
# `git rev-parse` — resolves `core.worktree` during repository setup, before it does the work asked
# of it. So a stale value, the worst case here because it points at a worktree since removed, makes
# even a plain config read die with "Invalid path", and a guard that took that for "not a checkout"
# would fall silent on exactly the repository it exists to catch. `GIT_WORK_TREE` overrides both
# offending settings, and `$PWD` is the right value by construction: this script lives in the
# checkout it judges, and the `cd` above put us at its root.
export GIT_WORK_TREE="$PWD"

# git translates its own messages, and a wrong locale would otherwise reshape the text below.
export LC_ALL=C

if ! command -v git >/dev/null 2>&1; then
  # A source export with no git at all: nothing to read, and nothing wrong.
  exit 0
fi

# "Is there a checkout here?" answered by the filesystem rather than by matching git's prose. A
# source export or a tarball has no `.git` at all; anything that *has* one and still cannot be
# opened is broken, and gets the loud treatment below. Matching git's own wording instead would read
# a pruned worktree admin directory, a missing `HEAD`, and a dangling `gitdir:` pointer as "no
# checkout here" — all of them states a poisoned repository reaches. `-e` rather than `-d`, because
# a linked worktree's `.git` is a file.
[ -e .git ] || exit 0

err_file="$(mktemp)"
trap 'rm -f "$err_file"' EXIT

report_git_failure() {
  echo "check-worktree-config: $1" >&2
  sed 's/^/  /' "$err_file" >&2
  echo "check-worktree-config: refusing to certify a repository that cannot be read." >&2
  exit 1
}

# stderr goes to its own file rather than into the captured value: git can warn (an unreadable
# global config, a credential helper) and still exit 0, and a warning glued onto the front of the
# path would point the read below at a file that cannot exist — passing the repository for want of a
# config to check.
#
# `--git-common-dir` rather than `--git-dir`, because the shared config belongs to the *main*
# checkout: in a linked worktree `--git-dir` names `.git/worktrees/<name>/`, which holds no `config`
# at all. And no `--path-format=absolute`, which needs git 2.31: `git rev-parse` answers an unknown
# option by echoing it and exiting 0, so on an older git the guard would take that echo for a path
# and quietly pass every repository.
common_dir="$(git rev-parse --git-common-dir 2>"$err_file")" ||
  report_git_failure "this directory has a .git, but git cannot read the repository:"

# `--git-common-dir` prints an absolute path from a linked worktree and a relative one from the main
# worktree, so absolutize it against the root the `cd` above put us at.
case "$common_dir" in
  /*) ;;
  *) common_dir="$PWD/$common_dir" ;;
esac

shared_config="$common_dir/config"
if [ ! -r "$shared_config" ]; then
  # The export case already exited above, so every remaining way to get here is a broken repository
  # rather than one with nothing to check.
  echo "check-worktree-config: $shared_config is missing or unreadable, so this repository's" >&2
  echo "check-worktree-config: shared configuration cannot be checked. See issue #1803." >&2
  exit 1
fi

# `--file` rather than a plain `git config` read: only the *shared* file is in question. A plain read
# would fold in the per-worktree config — where both settings are legitimate — along with the global
# and system files, and report a problem that is not there.
#
# The answer lands in `shared_value` instead of on stdout so that an unreadable file can exit the
# script. Returned through a command substitution, the `exit` below would only end the subshell.
shared_value=""
shared_found=0
shared_read() {
  local status=0
  # `--type=bool` where the caller asks for one, so git decides what counts as true: it accepts
  # `yes`, `on`, and `1` as readily as `true`, and a string comparison would wave every spelling but
  # one straight through.
  if [ -n "${2:-}" ]; then
    shared_value="$(git config --file "$shared_config" --type="$2" --get "$1" 2>"$err_file")" || status=$?
  else
    shared_value="$(git config --file "$shared_config" --get "$1" 2>"$err_file")" || status=$?
  fi
  case "$status" in
    0) shared_found=1 ;;
    # git exits 1 for "the key is absent" and 128 for "could not read at all". Only the first is an
    # answer; folding the second into it would clear a repository whose config git cannot parse.
    1)
      shared_value=""
      shared_found=0
      ;;
    *) report_git_failure "could not read '$1' from $shared_config:" ;;
  esac
}

shared_read extensions.worktreeConfig bool
[ "$shared_value" = "true" ] || exit 0

# Accumulated as text rather than as an array: bash 3.2 — still the stock shell on macOS, a
# first-class target here — treats an empty array as unset under `set -u`.
offenders=""

# Presence, not a non-empty value: a shared `core.worktree` set to the empty string offends just as
# much, and leaves git unable to run at all ("cannot chdir to ''"). Passing it for want of a value to
# print would hand the next command that cryptic message instead of this one's remedy.
shared_read core.worktree
worktree_value="$shared_value"
worktree_present="$shared_found"
if [ "$worktree_present" -eq 1 ]; then
  offenders="${offenders}    core.worktree = ${worktree_value}
"
fi

# Only `true` offends: git-worktree(1) singles out that value, and a shared `core.bare = false` is
# both harmless and what a normal clone carries.
shared_read core.bare bool
bare_value=""
if [ "$shared_value" = "true" ]; then
  bare_value="true"
  offenders="${offenders}    core.bare = true
"
fi

[ -n "$offenders" ] || exit 0

{
  echo "check-worktree-config: a per-worktree git setting is in the SHARED config, so it applies to"
  echo "check-worktree-config: every worktree of this repository (extensions.worktreeConfig is on):"
  echo
  printf '%s' "$offenders"
  echo
  echo "  in: $shared_config"
  echo
  echo "  Left in place, git silently reads a different working tree than your shell's directory:"
  echo "  'git status' lists another branch's files, 'git add' succeeds without changing anything,"
  echo "  'git commit --amend' drops files, and 'git checkout -- <path>' writes into another"
  echo "  session's worktree. See issue #1803."
  echo
  echo "  Clear it from the shared config, then re-run:"
  # `--unset-all` rather than `--unset`, which refuses (exit 5) when the key carries more than one
  # value and so would leave the reader following a command that changes nothing. For the ordinary
  # single value the two behave identically.
  if [ "$worktree_present" -eq 1 ]; then
    echo "      GIT_WORK_TREE=. git config --unset-all core.worktree"
  fi
  if [ -n "$bare_value" ]; then
    echo "      GIT_WORK_TREE=. git config --unset-all core.bare"
  fi
  echo
  # Without the prefix the remedy dies in the very state that motivates it, and a reader who takes
  # the prefix for noise and drops it gets that failure with no idea why.
  echo "  The 'GIT_WORK_TREE=.' prefix is not optional: git resolves core.worktree before it runs"
  echo "  the command you asked for, so once the setting points at a worktree that has been"
  echo "  removed, these commands themselves die with \"Invalid path\" until it is overridden."
  echo
  # `--worktree` alone would leave the shared value in place, still governing every other worktree
  # while this one looks repaired — which is how the misconfiguration went unnoticed before.
  echo "  Repairing only the worktree in hand ('git config --worktree core.bare false') leaves the"
  echo "  shared value governing every other worktree, so clear the shared config as above. A"
  echo "  worktree that genuinely needs either setting — a bare main repository keeping"
  echo "  core.bare = true — then adds it back with 'git config --worktree', never to the shared"
  echo "  file. See docs/ai-development.md#isolate-concurrent-sessions-with-worktrees."
} >&2

exit 1
