---
name: fix-issue
model: opus
description: Ship a plain GitHub issue — a small bug, papercut, or scoped improvement with no roadmap item — end to end, including tests, verification, a Draft PR, and follow-up.
---

# Claude adapter

Read `.agent-workflows/fix-issue/workflow.md` completely, then follow it.

Use Claude Code facilities for host-specific steps:

- Use the Agent tool for independent review and follow-up subagents.
- Run step 5's two roles on different models (BE-0347): `fable` for the review/plan pass, and for the implement pass `sonnet` when the fix stays within `docs/`, `opus` when it touches product code.
- Use the installed `pr-review-toolkit` for the specialized review pass.
- Use `/loop` for the paced post-PR follow-up the workflow's step 8 describes.
- Treat `.claude/settings.json` and `.claude/hooks/session-start.sh` as Claude-only configuration.
