---
name: git-sync
model: haiku
description: Fetch origin/main, rebase the current branch, and optionally prepare a topic worktree. Use for repository synchronization and worktree setup only.
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

   Claude Code keeps its own trees under `.claude/worktrees/`. When this checkout already sits
   there, stay in it — only create a second tree when the user asks for one.

   Otherwise use the project's `make worktree` target. Do not invent an ad-hoc `git worktree add`
   path.

   ```bash
   make worktree TOPIC=<topic>
   ```

   This fetches `origin/main`, creates `../bajutsu-<topic>` on branch `claude/<topic>`,
   and runs `make setup` inside. Report the worktree path when done.

   If the user specifies a `PREFIX` (for example their username), pass it through:

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
session with the implement-be workflow (or the appropriate skill).
