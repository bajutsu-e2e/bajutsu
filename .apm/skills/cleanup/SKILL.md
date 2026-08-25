---
name: cleanup
model: haiku
description: Clean up completed worktrees and merged branches after confirmation. Use when the user asks to remove stale worktrees or branches.
---

# Worktree and branch cleanup

Remove worktrees and branches whose work has already merged into `origin/main`. This is a
**mechanical, destructive-with-confirmation** skill — it never writes code.

**Decide nothing yourself.** [`scripts/worktree_cleanup.sh`](../../../scripts/worktree_cleanup.sh)
holds every safety rule, refuses on its own, and is the only way this skill touches a worktree or a
branch. Do not run `git worktree remove`, `git branch -d`, or `git branch -D` directly, and do not
reason from `git worktree list` or `git branch --merged` about what is safe to delete. Those two
commands are what caused [BE-0391](../../../roadmaps/BE-0391-cleanup-live-worktree-guards/BE-0391-cleanup-live-worktree-guards.md):
a branch created off `origin/main` that has not committed anything yet *is* `origin/main`, so
`--merged` lists it and `git branch -d` deletes it. For such a branch, "merged" means the work has
not started — the state of a session that is running right now.

## Steps

1. **Audit.** The script fetches `origin`, then prints every worktree with its path, its branch, and
   a verdict of `REMOVABLE` or `KEEP` with the reasons.

   ```bash
   scripts/worktree_cleanup.sh
   ```

2. **Show the user the audit output as it stands**, and ask for confirmation. Quote the script's
   own path and branch lines for each `REMOVABLE` entry. Never describe a worktree by its directory
   name alone: a directory is often named after a different topic than the branch it now holds, so
   the name can describe work that merged long ago while the branch inside belongs to a live session.
   Wait for an explicit yes in the conversation: removal is destructive, so an earlier answer never
   carries over to a later run.

3. **Remove each confirmed worktree, one at a time.** The script re-checks every guard before it
   acts, so it refuses a worktree that became busy since the audit.

   ```bash
   scripts/worktree_cleanup.sh --remove <path>
   ```

   A refusal is an answer, not an obstacle. Report it and move on — never work around it by running
   git directly, by passing `--force`, or by widening the staleness window.

4. **Prune** stale worktree metadata:

   ```bash
   git worktree prune
   ```

5. **Report** what was removed, and what was kept and why.

## What the script refuses

A worktree is removable only when all of these hold. Each check fails closed, so an answer the
script cannot obtain is a refusal.

- It is neither the main checkout nor the worktree this cleanup runs in.
- It carries no lock. Claude Code writes a lock naming the live session and its process id.
- It is present and not prunable, and its `HEAD` is attached to a branch.
- Its working tree has no uncommitted or untracked changes.
- Nothing under it changed within the staleness window (180 minutes by default,
  `BAJUTSU_CLEANUP_STALE_MINUTES` to override). A live session's tree is clean between edits, so
  modification time is what shows the session is still there.
- Its branch has no commits absent from `origin/main`, **and** its branch has a merged pull request.
  Ancestry alone cannot tell work that finished from work that never started; only a merged pull
  request shows the work was delivered.

## What this skill does NOT do

- Remove a worktree that any session is using.
- Delete a branch whose work has not landed in a merged pull request.
- Force anything: never `git worktree remove --force`, never `git branch -D`.
- Write code, run tests, or create PRs.
