---
name: task-select
model: sonnet
description: >-
  Select the next task to work on from GitHub Issues and the roadmap. Use when the user
  says "次のタスクを検討して", "タスクを選定して", "issueから次に実行するべきタスクを",
  "次に進めるべきタスクを", or asks to pick the next item to implement. Reads open GitHub
  Issues and roadmap status, filters by criteria (e.g. Proposal status), and presents
  ranked candidates with rationale. Read-only — it never implements, creates branches,
  or opens PRs.
---

# Task selection

Survey open GitHub Issues and the roadmap to recommend the next task to work on.
This is a **read-only, advisory** skill — it never implements features or creates branches.

## Steps

1. **Gather context**

   - Fetch open GitHub Issues:
     ```bash
     gh issue list --state open --limit 50
     ```
   - Check the roadmap for Proposal / In progress items:
     use the `/roadmap-filter` skill or read `roadmaps/README.md` directly.
   - Check for in-flight work (open PRs, branches) to avoid conflicts:
     ```bash
     gh pr list --state open --limit 30
     ```

2. **Filter** by the user's criteria if given (e.g. "Proposal status only",
   "exclude in-progress items", specific topics).

3. **Rank candidates** considering:
   - Dependencies: items that unblock others rank higher
   - Complexity: prefer items that can be completed in a single session
   - Recency: recently created proposals may have fresher context
   - Topic clustering: items in the same area can share context

4. **Present** a short ranked list (3–5 candidates) with:
   - BE ID and title
   - One-line rationale for why it's a good next pick
   - Any blockers or dependencies to be aware of

5. **Wait for the user's choice** before suggesting next steps. When the user
   picks a task, recommend:
   - `/git-sync <topic>` to prepare the worktree (Haiku)
   - `/implement-be BE-NNNN` to start implementation (Opus)

## What this skill does NOT do

- Implement features or write code
- Create branches, worktrees, or PRs
- Change roadmap status or metadata
