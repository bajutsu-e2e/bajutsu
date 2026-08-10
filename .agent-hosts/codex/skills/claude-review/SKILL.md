---
name: claude-review
description: Judge a working diff or pull request against this repo's CI review contract (.github/claude-review-prompt.md) — the same judgment the "Claude review" GitHub Actions workflow applies. Classifies findings; never edits files.
---

# Codex adapter

Read `.agent-workflows/claude-review/workflow.md` completely, then follow it.
Post inline PR comments with connected GitHub tools or `gh api` directly; do not invoke Claude
Code's Agent tool or `/loop`.
