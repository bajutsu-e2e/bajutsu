---
name: implement-be
description: Implement an existing numbered Bajutsu Evolution roadmap item end to end, including tests, roadmap status, verification, a Draft PR, and follow-up.
---

# Codex adapter

Read `.agent-workflows/implement-be/workflow.md` completely, then follow it.

Use Codex facilities for host-specific steps:

- Use fresh collaboration subagents for independent review and focused follow-up work.
- Use the connected GitHub tools for PR state and review comments, with `gh` only where required.
- Use bounded task waits or monitoring rather than Claude Code's `/loop`.
- Follow `AGENTS.md`; do not treat `.claude/settings.json`, Claude hooks, or Claude plugins as Codex configuration.
