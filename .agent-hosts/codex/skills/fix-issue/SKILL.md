---
name: fix-issue
description: Ship a plain GitHub issue — a small bug, papercut, or scoped improvement with no roadmap item — end to end, including tests, verification, a Draft PR, and follow-up.
---

# Codex adapter

Read `.agent-workflows/fix-issue/workflow.md` completely, then follow it.

Use Codex facilities for host-specific steps:

- Use fresh collaboration subagents for independent review and focused follow-up work.
- Use the connected GitHub tools for issue state, PR state, and review comments, with `gh` only where required.
- Use bounded task waits or monitoring rather than Claude Code's `/loop`.
- Follow `AGENTS.md`; do not treat `.claude/settings.json`, Claude hooks, or Claude plugins as Codex configuration.
