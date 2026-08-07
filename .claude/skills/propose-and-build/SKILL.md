---
name: propose-and-build
model: opus
description: Author a small, well-scoped Bajutsu Evolution proposal and its implementation together in one PR.
---

# Claude adapter

Read `.agent-workflows/propose-and-build/workflow.md` completely, then follow it.
Use a fresh Claude Code subagent through the Agent tool for independent review.
Run the self-review's two roles on different models (BE-0347): `fable` for the review/plan pass, and for the implement pass `sonnet` in Phase A, where the diff is roadmap prose, `opus` in Phase B, where it is product code.
