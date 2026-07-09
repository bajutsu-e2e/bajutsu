---
name: git-sync
model: haiku
description: >-
  Fetch the latest origin/main and rebase the current branch onto it, then optionally
  create a worktree for a given topic. Use when the user says "mainを最新化して",
  "git sync", "worktreeを切って", or asks to prepare a branch before starting work.
  This is a mechanical operation — it runs git commands and reports the result. It never
  implements features, writes tests, or opens PRs.
---

# Git sync + worktree preparation

Bring the local repo up to date and (optionally) set up an isolated worktree for a topic.
This is a **mechanical, command-only** skill — no design decisions, no code changes.

## Steps

1. **Fetch and rebase**

   ```bash
   git fetch origin
   git rebase origin/main
   ```

   If there are conflicts, report them and stop — don't resolve automatically.

2. **Worktree creation (when a topic is given)**

   Use the project's `make worktree` target:

   ```bash
   make worktree TOPIC=<topic>
   ```

   This fetches `origin/main`, creates `../bajutsu-<topic>` on branch `claude/<topic>`,
   and runs `make setup` inside. Report the worktree path when done.

   If the user specifies a `PREFIX` (e.g. their username), pass it through:

   ```bash
   make worktree TOPIC=<topic> PREFIX=<user>
   ```

3. **Report** the result: current branch, HEAD commit, worktree path (if created).

## What this skill does NOT do

- Implement features or write code
- Run `make check` or tests
- Create PRs or commits
- Resolve merge conflicts (report and stop)

If the user asks to proceed with implementation after sync, tell them to start a new
session with `/implement-be` or the appropriate skill — that will use the right model.
