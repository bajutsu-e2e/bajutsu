---
name: task-select
model: sonnet
description: >-
  Rank candidate Bajutsu tasks from GitHub Issues and the roadmap. Use when the user says
  "次のタスクを検討して", "タスクを選定して", "次に進めるべきタスクを", or asks to pick the next item to
  work on. Read-only — it never implements, creates branches, or opens PRs.
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
   - prepare a topic worktree with the git-sync workflow
   - the skill that ships the chosen candidate, which depends on what it is:
     - a numbered roadmap item → `implement-be BE-NNNN`
     - a bare GitHub issue with no BE id → `fix-issue #<N>`, the sibling skill that ships a
       plain issue through the same implementation, review, and gate steps
       ([`fix-issue`](../../../.apm/skills/fix-issue/SKILL.md))

## What this skill does NOT do

- Implement features or write code
- Create branches, worktrees, or PRs
- Change roadmap status or metadata
