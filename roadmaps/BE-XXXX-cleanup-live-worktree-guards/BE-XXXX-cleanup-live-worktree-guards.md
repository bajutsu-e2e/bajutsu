**English** · [日本語](BE-XXXX-cleanup-live-worktree-guards-ja.md)

# BE-XXXX — Guard worktree cleanup against live sessions and undelivered branches

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-cleanup-live-worktree-guards.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1730](https://github.com/bajutsu-e2e/bajutsu/pull/1730) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

The `cleanup` skill removes git worktrees and branches whose work has already merged into
`origin/main`, so that a machine running many parallel sessions does not accumulate dead
checkouts. On 2026-08-24 it removed the worktree of a session that was still running, and deleted
that session's branch. Every command it issued was the command the skill prescribed, and each
behaved exactly as git documents. We propose replacing the skill's safety predicate, and moving the
predicate out of prose into a script, `scripts/worktree_cleanup.sh`, that the skill must call for
both the audit and the removal.

## Motivation

The skill decided what was safe to remove by listing `git branch --merged origin/main` and treating
membership in that list as proof the work had finished. A branch created off `origin/main` that has
not committed anything yet points at `origin/main` itself, so git lists it as merged — correctly,
because the branch is an ancestor of `origin/main`. For a branch in that state, "merged" means the
work has not started, which is precisely the state of a session that has just begun. The predicate
therefore inverted its own intent: it was most confident about exactly the branches it should have
protected.

Two further gaps let the mistake reach the filesystem. The skill derived nothing from the worktree's
own contents, so it could not notice that a session was using the checkout; and it presented each
candidate to the human by path, while the path and the branch it holds routinely disagree. In the
incident the directory `.claude/worktrees/be-0369-implementation-8816c4` held the branch
`claude/implement-be-0390-65f926`, because an earlier session's directory had been reused. The
human approving the removal read a directory name that described work merged six days earlier,
while the branch inside it belonged to a session that was running at that moment.

No part of git refused the operation, because no part of git had grounds to refuse. The working tree
was clean at that instant, which is ordinary for a session between edits, so `git worktree remove`
proceeded without `--force`. The branch held no commits of its own, so `git branch -d` deleted it
without the warning that `-d` exists to give. The skill's own rule — use `-d`, never `-D` — held
throughout and prevented nothing.

A later reader can tell whether this change arrived by running the cleanup audit against a
repository that contains a freshly branched, actively used worktree: the audit must report that
worktree as one to keep, and name the reason. No cleanup run should remove a worktree whose branch
has no merged pull request.

## Detailed design

### The guard script

`scripts/worktree_cleanup.sh` audits by default and changes nothing. Given `--remove <path>` it
removes exactly one worktree, and re-evaluates every guard immediately beforehand, so a stale audit
can never authorize a removal. The script resolves each worktree's branch from
`git worktree list --porcelain`, the only output where the path-to-branch mapping is authoritative,
and reports both the path and the branch so that a human reading a confirmation prompt sees the work
that is actually at stake.

### The guards

A worktree is removable only when every guard below passes. Each guard fails closed: an answer the
script cannot obtain counts as a refusal, never as a pass.

1. The worktree is neither the main checkout nor the worktree the cleanup itself is running in.
2. The worktree carries no lock. Claude Code writes a lock naming the session and its process id,
   which makes an active session visible to any host that reads git's own metadata.
3. The worktree is present and not prunable.
4. `HEAD` is attached to a branch, since a detached `HEAD` offers no branch whose delivery could be
   checked.
5. The working tree holds no uncommitted or untracked changes.
6. No file under the worktree changed within the staleness window, which defaults to 180 minutes and
   is overridable through `BAJUTSU_CLEANUP_STALE_MINUTES`. A live session holds a clean tree between
   edits, so file modification time is the signal that the session is still there. The script also
   refuses a window that is not a whole number of minutes. `find` treats such a value as an error,
   and the error goes unseen. The empty answer left behind then reads as an idle worktree. A
   malformed window deletes this guard rather than widening it.
7. The branch has no commits absent from `origin/main`.
8. The branch has a merged pull request. This guard is the one that catches the branch that has
   never committed: ancestry tests cannot distinguish work that finished from work that never
   started, and only a merged pull request shows that the work was delivered.

Guards 7 and 8 run together and stay independent. A repository that squash-merges would need guard 8
alone, because a squashed branch's commits never appear on `origin/main`; this repository
fast-forwards, so guard 7 remains strict.

The script never passes `--force` to `git worktree remove` and never passes `-D` to `git branch`.
Both refusals are the last line of defence behind the guards above, and both flags exist only to
switch that refusal off.

### The skill

The `cleanup` workflow no longer describes git commands. It runs the audit, shows the human the
audit's own output, and removes one worktree at a time through `--remove`. Because the script
refuses rather than reports, a skill that misreads the audit still cannot remove a live worktree.

### Work breakdown

- The guard script and its registration in the shellcheck target.
- The regression tests, including the incident's exact shape.
- The rewritten workflow and its host adapters.

## Alternatives considered

**Tighten the workflow's prose.** Rejected: the prose was already correct, and the skill followed
it. Restating a rule that held cannot prevent a failure that occurred while the rule was obeyed.

**Add a commit-count check beside `git branch --merged`.** Rejected as insufficient on its own. It
catches the branch with no commits, but still clears a branch whose commits have merged while a
session continues to work in the checkout.

**Rely on `git worktree remove` refusing a dirty tree.** Rejected: the incident's tree was clean.
A session's working tree is clean whenever the session is between edits, so the refusal that git
offers here does not track whether anyone is using the checkout.

**Lock every session's worktree and check only the lock.** Adopted in part, as guard 2, but not
relied on alone. A lock only helps for hosts that write one, and the guard must hold for a worktree
created by any host or by hand.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Guard script `scripts/worktree_cleanup.sh`, registered in the `lint-sh` target.
- [x] Regression tests in `tests/test_worktree_cleanup.py`, covering the incident's exact shape.
- [x] Rewritten `cleanup` workflow and its Claude and Codex adapters.

Log:

- 2026-08-24 — Investigated the incident from the session transcripts, established that every
  command the skill ran behaved as documented, and shipped the guard script, its tests, and the
  rewritten workflow.

## References

- [`.agent-workflows/cleanup/workflow.md`](../../.agent-workflows/cleanup/workflow.md) — the
  workflow this item rewrites.
- [`docs/ai-development.md`](../../docs/ai-development.md#isolate-concurrent-sessions-with-worktrees)
  — the worktree recipe that creates the checkouts this skill later removes.
- [BE-0390](../BE-0390-apm-skill-management/BE-0390-apm-skill-management.md) — moves the skill
  layout to `.apm/skills/`, which will carry the rewritten workflow.
