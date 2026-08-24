---
name: cleanup
model: haiku
description: Clean up completed worktrees and merged branches after confirmation. Use when the user asks to remove stale worktrees or branches.
---

# Claude adapter

Read `.agent-workflows/cleanup/workflow.md` completely, then follow it.
Use Claude Code's native confirmation and shell facilities for the workflow.

Every removal goes through `scripts/worktree_cleanup.sh`. Never call `git worktree remove` or
`git branch -d` / `-D` yourself, and if a Bash call is denied, report the denial rather than
reaching the same result through a different command.
