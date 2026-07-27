---
name: git-sync
model: haiku
description: Fetch origin/main, rebase the current branch, and optionally prepare a topic worktree. Use for repository synchronization and worktree setup only.
---

# Claude adapter

Read `.agent-workflows/git-sync/workflow.md` completely, then follow it.
Use the repository's Claude branch convention where the workflow requires a new branch.

If Claude Code already placed this checkout under `.claude/worktrees/`, keep that tree.
Do not create a second tree with `make worktree` unless the user asks for one.
