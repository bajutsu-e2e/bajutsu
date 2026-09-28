#!/usr/bin/env bash
# Refuse to run when a git setting that must not be shared has been written to the shared config, or
# when a placeholder identity has leaked into the environment — three ways for a wrong git identity
# or worktree setting to take hold, the first two already real incidents (issue #1803).
#
# 1. `core.worktree` / `core.bare = true`. With `extensions.worktreeConfig` enabled, git drops its
#    built-in exception confining these two to the main working tree. A shared value then applies to
#    *every* worktree, and git-worktree(1) says so plainly: `core.worktree` "should never be shared",
#    and `core.bare` "should not be shared if the value is core.bare=true". A shared `core.worktree`
#    makes every command in every worktree resolve to one other tree's directory while `--git-dir`
#    still answers locally: `git status` lists another branch's files, `git add` reports success and
#    changes nothing, `git commit --amend` drops a file from the commit, and `git checkout -- <path>`
#    writes into a concurrent session's worktree. A shared `core.bare = true` is the loud half —
#    every invocation fails with "this operation must be run in a work tree" — but it hides behind
#    the first until that one is cleared.
# 2. `user.email` / `user.name` at a test fixture's placeholder identity (`t@example.com`, and
#    similar). A throwaway repo built by a test is meant to carry its own committer identity, set
#    with a real, persistent `git config` write — but if the process that runs it has inherited
#    `GIT_DIR` (a git hook exports it into everything it runs), that write lands in the shared config
#    of whichever repository `GIT_DIR` names instead of the throwaway one, silently overriding every
#    contributor's real identity for every future commit in every worktree. This has already
#    happened to this repository more than once.
# 3. `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_EMAIL` at the same kind of placeholder identity — not a
#    config file at all. git's identity resolution puts these environment variables above every
#    config scope (worktree, local, global, system), so a per-directory environment tool such as
#    direnv's `.envrc` can silently override even a correctly configured `user.email` for as long as
#    it stays exported. Checked defensively, alongside 1 and 2 rather than as their own incident:
#    this repository does not use direnv today, but the checks above would report a clean bill of
#    health while every commit still used a poisoned identity from the environment. Only the same
#    reserved-domain pattern as #2 is ever flagged — a real address a contributor deliberately sets
#    this way (a work address via one project's `.envrc`, say) is exactly what these variables are
#    for, and is left alone as the correct value, never reported.
#
# All three failures are silent, not loud, which is why this guard exists rather than a note in the
# docs.
#
# This only reports. Every offending setting or variable arrives from outside this repository's
# tooling (nothing here writes or exports any of them), and the correct repair can depend on whether
# the checkout is the main or a linked worktree, so the remedy is printed for a human to apply rather
# than guessed at.
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

# Whether an email is an RFC 2606/6761 reserved example/test/local domain, or a subdomain of one. No
# real contributor's identity ever resolves to one of these, in any subdomain, so a match is never a
# false positive the way a plain "differs from global" (or "differs from what direnv set") check
# would be — plenty of contributors legitimately set a repo-local, or environment-provided, identity
# that has nothing to do with either incident this guard exists for. Shared by every place below that
# checks an identity, whether the source is the shared config file or an environment variable.
is_placeholder_email() {
  case "$1" in
    *@example.com | *@example.net | *@example.org | \
      *@*.example.com | *@*.example.net | *@*.example.org | \
      *@*.example | *@*.invalid | *@*.test | *@localhost | *@*.localhost)
      return 0
      ;;
    *) return 1 ;;
  esac
}

# Accumulated as text rather than as an array: bash 3.2 — still the stock shell on macOS, a
# first-class target here — treats an empty array as unset under `set -u`.
offenders=""

# `user.email` at a placeholder identity is checked unconditionally — unlike core.worktree/core.bare
# below, this offense has nothing to do with extensions.worktreeConfig: even without that extension,
# everything in the shared file already governs every worktree, because there is nowhere else for a
# worktree's own config to live. It is issue #1803's sibling: a test fixture's `git -C <tmp-repo>
# config user.email t@example.com`, meant for a throwaway repo, lands in the shared file instead
# because an inherited GIT_DIR overrode `-C`.
shared_read user.email
email_value="$shared_value"
placeholder_identity=0
if [ "$shared_found" -eq 1 ] && is_placeholder_email "$email_value"; then
  placeholder_identity=1
  offenders="${offenders}    user.email = ${email_value}
"
fi

# Reported only alongside a placeholder email: pairing the two is what a leaked fixture writes, and a
# `user.name` on its own (an initial, a nickname) is unremarkable and none of this guard's business.
name_present=0
if [ "$placeholder_identity" -eq 1 ]; then
  shared_read user.name
  name_value="$shared_value"
  name_present="$shared_found"
  if [ "$name_present" -eq 1 ]; then
    offenders="${offenders}    user.name = ${name_value}
"
  fi
fi

# GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL at a placeholder identity — checked the same way and for the
# same reason as user.email above, just from the environment instead of a file. Left untouched by the
# GIT_* unset above (that list is only ever the repository-location variables), so this sees exactly
# what a real `git commit` in this shell would: whatever a per-directory tool such as direnv exported,
# unfiltered. A real address set this way is the correct value and is never reported — only the exact
# placeholder pattern is.
identity_env_offense=0
env_author_email="${GIT_AUTHOR_EMAIL:-}"
if [ -n "$env_author_email" ] && is_placeholder_email "$env_author_email"; then
  identity_env_offense=1
  offenders="${offenders}    \$GIT_AUTHOR_EMAIL = ${env_author_email}
"
fi
env_committer_email="${GIT_COMMITTER_EMAIL:-}"
if [ -n "$env_committer_email" ] && is_placeholder_email "$env_committer_email"; then
  identity_env_offense=1
  offenders="${offenders}    \$GIT_COMMITTER_EMAIL = ${env_committer_email}
"
fi

shared_read extensions.worktreeConfig bool
worktree_config_on="$shared_value"

# Read unconditionally, independent of extensions.worktreeConfig: git's built-in exception confines a
# shared core.worktree to the *main* checkout rather than disabling it, so even with the extension
# off it still governs this checkout when this checkout is the main one. Every remedy command below —
# including a same-command identity unset that has nothing to do with worktrees — resolves
# core.worktree during repository setup, so the prefix that survives it has to be decided from
# presence alone, before deciding whether core.worktree is itself being reported as an offense.
#
# Presence, not a non-empty value: a shared `core.worktree` set to the empty string offends just as
# much, and leaves git unable to run at all ("cannot chdir to ''"). Passing it for want of a value to
# print would hand the next command that cryptic message instead of this one's remedy.
shared_read core.worktree
worktree_value="$shared_value"
worktree_present="$shared_found"

# Only reported as an offense when the extension is on: without it, a shared core.worktree is git's
# own documented exception (confined to the main checkout), not a misconfiguration this guard names.
worktree_is_offense=0
if [ "$worktree_config_on" = "true" ] && [ "$worktree_present" -eq 1 ]; then
  worktree_is_offense=1
  offenders="${offenders}    core.worktree = ${worktree_value}
"
fi

# Only `true` offends: git-worktree(1) singles out that value, and a shared `core.bare = false` is
# both harmless and what a normal clone carries. Also gated on the extension, for the same reason as
# core.worktree above.
bare_value=""
if [ "$worktree_config_on" = "true" ]; then
  shared_read core.bare bool
  if [ "$shared_value" = "true" ]; then
    bare_value="true"
    offenders="${offenders}    core.bare = true
"
  fi
fi

[ -n "$offenders" ] || exit 0

# core.worktree, present and pointing anywhere at all — even a path that no longer exists — makes
# every later git command in this script (the remedy lines included) resolve it during repository
# setup and die with "Invalid path" unless overridden. The prefix below is what survives that, and it
# is needed whenever core.worktree is merely *present*, whether or not it is being reported above.
work_tree_prefix=""
if [ "$worktree_present" -eq 1 ]; then
  work_tree_prefix="GIT_WORK_TREE=. "
fi

worktree_offense=0
if [ "$worktree_is_offense" -eq 1 ] || [ -n "$bare_value" ]; then
  worktree_offense=1
fi

# Whether anything reported above actually lives in the shared config file — as opposed to only the
# environment — decides whether "in: $shared_config" and its unset remedy make sense to print at all.
config_offense=0
if [ "$placeholder_identity" -eq 1 ] || [ "$worktree_offense" -eq 1 ]; then
  config_offense=1
fi

{
  if [ "$config_offense" -eq 1 ] && [ "$identity_env_offense" -eq 1 ]; then
    echo "check-worktree-config: a git setting that must not be shared, and a placeholder identity in"
    echo "check-worktree-config: the environment, are both present:"
  elif [ "$config_offense" -eq 1 ]; then
    echo "check-worktree-config: a git setting that must not be shared is in the SHARED config:"
  else
    echo "check-worktree-config: a placeholder git identity is set in the environment:"
  fi
  echo
  printf '%s' "$offenders"
  echo

  if [ "$config_offense" -eq 1 ]; then
    echo "  in: $shared_config"
    echo
  fi

  if [ "$worktree_offense" -eq 1 ]; then
    echo "  core.worktree/core.bare apply to *every* worktree of this repository once here"
    echo "  (extensions.worktreeConfig is on) — the setting git-worktree(1) says should never be"
    echo "  shared. Left in place, git silently reads a different working tree than your shell's"
    echo "  directory: 'git status' lists another branch's files, 'git add' succeeds without"
    echo "  changing anything, 'git commit --amend' drops files, and 'git checkout -- <path>' writes"
    echo "  into another session's worktree. See issue #1803."
    echo
  fi

  if [ "$placeholder_identity" -eq 1 ]; then
    echo "  user.email/user.name here silently override every contributor's real git identity, in"
    echo "  every worktree, for every commit, until someone notices by reading 'git log' closely —"
    echo "  as happened before. It is issue #1803's sibling bug: a test fixture's throwaway-repo"
    echo "  'git config' write missed its target because an inherited GIT_DIR overrode '-C'."
    echo
  fi

  if [ "$identity_env_offense" -eq 1 ]; then
    echo "  \$GIT_AUTHOR_EMAIL/\$GIT_COMMITTER_EMAIL override every config file's identity — including"
    echo "  a correct one — for as long as they stay exported in this shell. A per-directory"
    echo "  environment tool such as direnv's .envrc is the usual source. There is no config file for"
    echo "  this guard to unset: find and remove the export (or fix the value) wherever it comes"
    echo "  from, then start a new shell. A real address set this way on purpose is never reported —"
    echo "  only this exact placeholder pattern is."
    echo
  fi

  if [ "$config_offense" -eq 1 ]; then
    echo "  Clear it from the shared config, then re-run:"
    # `--unset-all` rather than `--unset`, which refuses (exit 5) when the key carries more than one
    # value and so would leave the reader following a command that changes nothing. For the ordinary
    # single value the two behave identically.
    if [ "$worktree_is_offense" -eq 1 ]; then
      echo "      ${work_tree_prefix}git config --unset-all core.worktree"
    fi
    if [ -n "$bare_value" ]; then
      echo "      ${work_tree_prefix}git config --unset-all core.bare"
    fi
    if [ "$placeholder_identity" -eq 1 ]; then
      echo "      ${work_tree_prefix}git config --unset-all user.email"
      if [ "$name_present" -eq 1 ]; then
        echo "      ${work_tree_prefix}git config --unset-all user.name"
      fi
    fi
    echo
  fi

  if [ -n "$work_tree_prefix" ]; then
    # Without the prefix the remedy dies in the very state that motivates it, and a reader who takes
    # the prefix for noise and drops it gets that failure with no idea why. It has to prefix every
    # line above too, including a same-command identity unset, since git resolves core.worktree
    # during repository setup regardless of which key the command is actually changing — and this can
    # fire even when core.worktree itself is not being reported as an offense (extension off).
    echo "  The 'GIT_WORK_TREE=.' prefix is not optional: git resolves core.worktree before it runs"
    echo "  the command you asked for, so once the setting points at a worktree that has been"
    echo "  removed, these commands themselves die with \"Invalid path\" until it is overridden."
    echo
  fi

  if [ "$worktree_offense" -eq 1 ]; then
    # `--worktree` alone would leave the shared value in place, still governing every other worktree
    # while this one looks repaired — which is how the misconfiguration went unnoticed before.
    echo "  Repairing only the worktree in hand ('git config --worktree core.bare false') leaves the"
    echo "  shared value governing every other worktree, so clear the shared config as above. A"
    echo "  worktree that genuinely needs either setting — a bare main repository keeping"
    echo "  core.bare = true — then adds it back with 'git config --worktree', never to the shared"
    echo "  file. See docs/ai-development.md#isolate-concurrent-sessions-with-worktrees."
  fi
} >&2

exit 1
