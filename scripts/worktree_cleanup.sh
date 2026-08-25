#!/usr/bin/env bash
# Audit and remove worktrees whose work has already landed on origin/main.
#
# The `cleanup` skill used to drive this by hand with `git worktree list` +
# `git branch --merged origin/main`, and that pair is not a safety check. A branch created off
# origin/main that has not committed anything yet sits *at* origin/main, so `--merged` lists it and
# `git branch -d` deletes it without complaint — both behave exactly as documented. "Merged" there
# means "has not started", not "is finished", which is precisely a live session's state. That is how
# an active session's worktree was removed out from under it (BE-0391). Prose could not fix it: the
# commands were correct, the predicate was wrong. So the predicate lives here, in one place, and the
# skill only ever calls this script.
#
# Usage:
#   scripts/worktree_cleanup.sh                  # audit: print a verdict per worktree, change nothing
#   scripts/worktree_cleanup.sh --remove <path>  # remove one worktree + its branch, guards re-checked
#
# --remove takes exactly one path and re-runs every guard immediately before acting, so a stale audit
# can never authorize a removal. Guards fail closed: anything unknown is a refusal, never a pass.
set -euo pipefail

# Guard 1 protects the worktree the *caller* is sitting in, which the `cd` below is about to lose:
# afterwards `--show-toplevel` names whichever checkout holds this copy of the script, so an
# absolute-path invocation from another worktree would leave that guard covering the wrong one.
invoked_from="$PWD"

cd "$(dirname "$0")/.."

# A live session can hold a momentarily clean tree (between edits, or mid-thinking), so mtime is the
# only signal that it is still there. Generous by default; override for a deliberate sweep of an
# idle machine.
STALE_MINUTES="${BAJUTSU_CLEANUP_STALE_MINUTES:-180}"
# A value `find -mmin` cannot parse deletes the staleness guard instead of widening it: find's usage
# error goes to /dev/null and its status is swallowed, leaving "no recent files" — a pass. Refuse the
# value here, where "anything unknown is a refusal" is still cheap to honour.
case "$STALE_MINUTES" in
  '' | *[!0-9]*)
    echo "cleanup: BAJUTSU_CLEANUP_STALE_MINUTES must be a whole number of minutes," \
      "got '$STALE_MINUTES'" >&2
    exit 1
    ;;
esac

main_worktree="$(git rev-parse --path-format=absolute --git-common-dir)"
main_worktree="$(cd "$(dirname "$main_worktree")" && pwd -P)"
# Fall back to this script's checkout when the caller stood outside a repository — there is no
# session worktree to protect in that case, and the main-checkout clause above still holds.
self_worktree="$(git -C "$invoked_from" rev-parse --path-format=absolute --show-toplevel 2>/dev/null \
  || git rev-parse --path-format=absolute --show-toplevel)"
self_worktree="$(cd "$self_worktree" && pwd -P)"

# `find` over a worktree must not walk build output: .venv alone is ~120 packages, and its mtimes
# reflect `uv sync`, not a human. Pruning them keeps the check both fast and honest.
readonly PRUNE_DIRS=(.git .venv node_modules runs tmp .build DerivedData)

# --- guard helpers ------------------------------------------------------------------------------

# Print each reason this worktree must not be removed, one per line. No output means removable.
refusals_for() {
  local path="$1" branch="$2" locked="$3" detached="$4" prunable="$5"
  local real
  real="$(cd "$path" 2>/dev/null && pwd -P)" || { echo "worktree directory is missing (run 'git worktree prune')"; return 0; }

  # Categorical refusals: no delivery check could ever make these removable, so stop here rather
  # than padding the report with checks whose answer does not matter.
  if [ "$real" = "$main_worktree" ]; then echo "this is the main checkout"; return 0; fi
  if [ "$real" = "$self_worktree" ]; then echo "this is the worktree cleanup is running in"; return 0; fi
  if [ -n "$locked" ]; then echo "worktree is locked ($locked)"; fi
  if [ -n "$prunable" ]; then echo "worktree is prunable, not removable ($prunable)"; fi

  if [ -n "$detached" ] || [ -z "$branch" ]; then
    echo "HEAD is detached, so there is no branch whose delivery can be verified"
    return 0
  fi

  if [ -n "$(git -C "$path" status --porcelain 2>/dev/null)" ]; then
    echo "working tree has uncommitted or untracked changes"
  fi

  local recent
  local -a prune_expr=()
  local d
  for d in "${PRUNE_DIRS[@]}"; do
    prune_expr+=(-name "$d" -prune -o)
  done
  recent="$(find "$real" "${prune_expr[@]}" -type f -mmin "-${STALE_MINUTES}" -print 2>/dev/null | head -n 1 || true)"
  if [ -n "$recent" ]; then
    echo "files changed in the last ${STALE_MINUTES} minutes, so a session is probably still using it"
  fi

  local ahead
  ahead="$(git rev-list --count "origin/main..refs/heads/$branch" 2>/dev/null || echo unknown)"
  if [ "$ahead" = unknown ]; then
    echo "cannot compare '$branch' against origin/main"
  elif [ "$ahead" != 0 ]; then
    echo "branch '$branch' has $ahead commit(s) not on origin/main"
  fi

  # The guard that the old --merged check could not express. A branch with zero commits passes every
  # ancestry test there is; only a merged pull request proves work was actually delivered. Squash- or
  # rebase-merged branches would need this to stand alone (their commits never appear on main) — this
  # repo fast-forwards, so both run together and the ancestry test above stays strict.
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh is unavailable, so the merged-PR check cannot run"
  else
    local merged
    merged="$(gh pr list --head "$branch" --state merged --json number --jq 'length' 2>/dev/null || echo unknown)"
    # Every value except a positive count refuses. `unknown` is only what a *silent* failure leaves
    # behind; a gh that prints something before failing (a wrapper, a shim, an auth notice) yields
    # neither `unknown` nor a count, and matching just those two would pass that off as a delivery
    # this script never confirmed.
    case "$merged" in
      '' | *[!0-9]*) echo "could not query pull requests for '$branch'" ;;
      0) echo "no merged pull request for '$branch' — its work never landed" ;;
    esac
  fi
  return 0
}

# Walk `git worktree list --porcelain` and call back with the parsed fields. The porcelain form is
# the only place the path→branch mapping is authoritative: a worktree directory is frequently named
# after a *different* topic than the branch it holds (a recycled directory), and reading the branch
# off the path is what makes a confirmation prompt describe the wrong work.
each_worktree() {
  local callback="$1"
  # Prefixed names on purpose: bash scopes dynamically, so a plain `branch` here would shadow the
  # caller's own `branch` and silently swallow whatever the callback assigns to it.
  local wt_line wt_path="" wt_branch="" wt_locked="" wt_detached="" wt_prunable=""
  while IFS= read -r wt_line; do
    case "$wt_line" in
      "worktree "*) wt_path="${wt_line#worktree }" ;;
      "branch "*)   wt_branch="${wt_line#branch refs/heads/}" ;;
      "detached")   wt_detached=1 ;;
      "locked"*)    wt_locked="${wt_line#locked}"; wt_locked="${wt_locked# }"; wt_locked="${wt_locked:-no reason given}" ;;
      "prunable"*)  wt_prunable="${wt_line#prunable}"; wt_prunable="${wt_prunable# }"; wt_prunable="${wt_prunable:-stale}" ;;
      "")
        if [ -n "$wt_path" ]; then
          "$callback" "$wt_path" "$wt_branch" "$wt_locked" "$wt_detached" "$wt_prunable"
        fi
        wt_path=""; wt_branch=""; wt_locked=""; wt_detached=""; wt_prunable=""
        ;;
    esac
  done < <(git worktree list --porcelain; echo)
}

report() {
  local path="$1" branch="$2"
  local reasons
  reasons="$(refusals_for "$@")"
  echo
  echo "  path:   $path"
  echo "  branch: ${branch:-(detached)}"
  if [ -z "$reasons" ]; then
    echo "  verdict: REMOVABLE"
  else
    echo "  verdict: KEEP"
    while IFS= read -r r; do echo "    - $r"; done <<<"$reasons"
  fi
}

# --- entry points -------------------------------------------------------------------------------

audit() {
  git fetch origin --quiet
  echo "Worktree cleanup audit (stale threshold: ${STALE_MINUTES} minutes)"
  each_worktree report
  echo
  echo "Nothing was changed. Remove one at a time with:"
  echo "  scripts/worktree_cleanup.sh --remove <path>"
}

remove_one() {
  local target="$1" real
  real="$(cd "$target" 2>/dev/null && pwd -P)" || {
    echo "cleanup: '$target' is not a directory; if its metadata is stale run 'git worktree prune'" >&2
    exit 1
  }

  git fetch origin --quiet

  local found="" branch="" reasons=""
  match() {
    local candidate="$1" this
    this="$(cd "$candidate" 2>/dev/null && pwd -P)" || return 0
    [ "$this" = "$real" ] || return 0
    found=1
    branch="$2"
    reasons="$(refusals_for "$@")"
  }
  each_worktree match

  if [ -z "$found" ]; then
    echo "cleanup: '$target' is not a registered worktree" >&2
    exit 1
  fi
  if [ -n "$reasons" ]; then
    echo "cleanup: refusing to remove $target (branch ${branch:-detached})" >&2
    while IFS= read -r r; do echo "  - $r" >&2; done <<<"$reasons"
    exit 1
  fi

  # No --force, ever: git's own dirty-tree refusal is the last line of defence behind these guards,
  # and --force is exactly what turns that refusal off.
  git worktree remove "$real"
  # -d, never -D: git refuses a branch whose commits are not merged.
  git branch -d "$branch"
  echo "cleanup: removed $target and branch $branch"
}

case "${1:-}" in
  "")        audit ;;
  --remove)  [ $# -eq 2 ] || { echo "cleanup: --remove takes exactly one worktree path" >&2; exit 1; }
             remove_one "$2" ;;
  *)         echo "usage: scripts/worktree_cleanup.sh [--remove <worktree-path>]" >&2; exit 1 ;;
esac
