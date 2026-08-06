---
name: pr-followup
model: sonnet
description: Fix CI failures, address review comments, reply with grounds, and resolve conversations after a PR is opened.
---

# Claude adapter

Read `.agent-workflows/pr-followup/workflow.md` completely, then follow it.
When invoked from `implement-be`, use Claude Code's Agent tool and `/loop` as directed by that adapter.
Run step 4's two roles on different models (BE-0347): `fable` for the review/plan pass, and for the implement pass `sonnet` when the fix stays within `roadmaps/` or `docs/`, `opus` when it touches product code.
