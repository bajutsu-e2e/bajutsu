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

# TOPIC and PREFIX are interpolated into a filesystem path (../bajutsu-<topic>) and a git ref
# (<prefix>/<topic>), so restrict both to a conservative slug — letters, digits, '.', '_', '-'.
# This refuses spaces, '/', '..' (path traversal / nested worktrees) and a leading '-' (parsed as
# a flag) before they reach `git worktree` as a confusing error or a surprising location.
for pair in "TOPIC:$topic" "PREFIX:$prefix"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  case "$value" in
    -*) echo "worktree: $name must not start with '-': '$value'" >&2; exit 1 ;;
    *..*) echo "worktree: $name must not contain '..': '$value'" >&2; exit 1 ;;
    *[!A-Za-z0-9._-]*) echo "worktree: $name may only contain letters, digits, '.', '_', '-': '$value'" >&2; exit 1 ;;
  esac
done

branch="$prefix/$topic"
path="../bajutsu-$topic"

# Always sync first so the new worktree branches off the latest origin/main, not a stale ref.
git fetch origin
git worktree add "$path" -b "$branch" origin/main

# Bootstrap the new tree exactly like a fresh clone (deps + self-healing git hooks).
make -C "$path" setup

echo "worktree: created $path on branch $branch (off origin/main)"
