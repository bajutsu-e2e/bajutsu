#!/usr/bin/env bash
# Create an isolated worktree + branch for a focused session, off the latest origin/main.
#
# The multi-step recipe in docs/ai-development.md ("Isolate concurrent sessions with
# worktrees") as one command (BE-0069 C). The `git fetch origin` is baked in and NOT
# optional: `origin/main` is a local tracking ref that only advances on fetch, so the
# foot-gun the docs warn about — branching off a stale main and re-introducing conflicts
# others already merged away — cannot happen here.
#
# Usage: scripts/worktree.sh <topic>   (Makefile: make worktree TOPIC=<topic> [PREFIX=<user>])
# The branch is <prefix>/<topic> (prefix defaults to `claude`); the worktree lands at
# ../bajutsu-<topic> next to this checkout.
set -euo pipefail

cd "$(dirname "$0")/.."

topic="${1:-}"
prefix="${PREFIX:-claude}"

if [ -z "$topic" ]; then
  echo "worktree: TOPIC is required, e.g. make worktree TOPIC=fix-foo" >&2
  exit 1
fi

branch="$prefix/$topic"
path="../bajutsu-$topic"

# Always sync first so the new worktree branches off the latest origin/main, not a stale ref.
git fetch origin
git worktree add "$path" -b "$branch" origin/main

# Bootstrap the new tree exactly like a fresh clone (deps + self-healing git hooks).
make -C "$path" setup

echo "worktree: created $path on branch $branch (off origin/main)"
