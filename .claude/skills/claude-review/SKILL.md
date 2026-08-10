---
name: claude-review
model: opus
description: Judge a working diff or pull request against this repo's CI review contract (.github/claude-review-prompt.md) — the same judgment the "Claude review" GitHub Actions workflow applies. Classifies findings; never edits files.
---

# Claude adapter

Read `.agent-workflows/claude-review/workflow.md` completely, then follow it.
When another skill's self-review loop spawns this as its review/plan pass, use Claude Code's Agent
tool to run it as a fresh subagent with no memory of the calling conversation — the caller may
override the model for that call (e.g. `pr-followup` uses `fable`).
When posting to a live PR, post inline comments with `gh api` directly — this interactive session
has no `mcp__github_inline_comment` tool, which exists only inside the claude-code-action
environment the CI workflow runs in.
