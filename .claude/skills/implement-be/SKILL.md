---
name: implement-be
model: opus
description: Implement an existing numbered Bajutsu Evolution roadmap item end to end, including tests, roadmap status, verification, a Draft PR, and follow-up.
---

# Claude adapter

Read `.agent-workflows/implement-be/workflow.md` completely, then follow it.

Use Claude Code facilities for host-specific steps:

- Use the Agent tool for independent review and follow-up subagents.
- Use the installed `pr-review-toolkit` for the specialized review pass.
- Use `/loop` for the paced post-PR follow-up described by the workflow.
- Treat `.claude/settings.json` and `.claude/hooks/session-start.sh` as Claude-only configuration.
