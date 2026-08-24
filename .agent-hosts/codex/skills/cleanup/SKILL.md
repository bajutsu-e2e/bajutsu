---
name: cleanup
description: Clean up completed worktrees and merged branches after confirmation. Use when the user asks to remove stale worktrees or branches.
---

# Codex adapter

Read `.agent-workflows/cleanup/workflow.md` completely, then follow it.
Use Codex approvals for destructive operations and report what can be recovered.

Every removal goes through `scripts/worktree_cleanup.sh`. Never call `git worktree remove` or
`git branch -d` / `-D` yourself, and if an approval is refused, report the refusal rather than
reaching the same result through a different command.
