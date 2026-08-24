---
name: implement-be
model: opus
description: Implement an existing numbered Bajutsu Evolution roadmap item end to end, including tests, roadmap status, verification, a Draft PR, and follow-up.
---

# Claude adapter

Read `.agent-workflows/implement-be/workflow.md` completely, then follow it.

Use Claude Code facilities for host-specific steps:

- Use the Agent tool for independent review and follow-up subagents.
- Send step 3's discovery to the `scout` agent rather than searching in the main thread — it
  runs on `fable` and returns paths, not file contents. Discovery that inherits this skill's
  `opus` puts the repo's cheapest work on its most expensive path: one recorded `Explore` run
  spent 430,695 tokens on `opus` to return 1,118 characters.
- Run step 7's two roles on different models (BE-0347): `fable` for the review/plan pass, and for the implement pass `sonnet` when the fix stays within `roadmaps/` or `docs/`, `opus` when it touches product code.
- Use the installed `pr-review-toolkit` for the specialized review pass.
- Use `/loop` for the paced post-PR follow-up described by the workflow.
- At the checkpoints the workflow names, invoke `be-progress-tracker` (Agent tool, `model:
  "haiku"`) — a subagent call doesn't inherit that skill's own frontmatter model, so pass it
  explicitly. Treat a failed or skipped checkpoint as advisory only, never a blocker.
- Treat `.claude/settings.json` and `.claude/hooks/session-start.sh` as Claude-only configuration.
