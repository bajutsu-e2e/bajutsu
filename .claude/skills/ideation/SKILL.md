---
name: ideation
model: sonnet
description: Turn a rough Bajutsu feature idea into a bilingual roadmap proposal. Use for proposal authoring and review, never implementation.
---

# Claude adapter

Read `.agent-workflows/ideation/workflow.md` completely, then follow it.
When the workflow requests an independent review, use a fresh Claude Code subagent through the Agent tool.
Send any discovery beyond step 1's queries to the `scout` agent, which runs on `fable` and returns paths rather than file contents.
Run step 5's two roles on different models (BE-0347): `fable` for the review/plan pass, which only judges, and `sonnet` for the implement pass, since this skill's fixes never leave `roadmaps/`.
At the checkpoints the workflow names, invoke `be-progress-tracker` (Agent tool, `model: "haiku"`) — a subagent call doesn't inherit that skill's own frontmatter model, so pass it explicitly. Treat a failed or skipped checkpoint as advisory only.
