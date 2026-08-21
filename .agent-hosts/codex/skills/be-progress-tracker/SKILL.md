---
name: be-progress-tracker
description: Create or update a per-BE-item status page (overview, implementation progress, work log). Called by ideation, implement-be, or propose-and-build at their own step boundaries — not run standalone.
---

# Codex adapter

Read `.agent-workflows/be-progress-tracker/workflow.md` completely, then follow it.

Codex has no hosted-Artifact equivalent, so write the status page to a local Markdown file instead
— under the repo's gitignored scratch area (e.g. `tmp/be-status/BE-<id>.md`), never committed. Keep
using the same path across every checkpoint for one BE id, and tell the user that path the first
time you create it so they can open it themselves.
