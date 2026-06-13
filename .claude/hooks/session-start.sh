#!/bin/bash
# SessionStart hook for Claude Code on the web.
#
# Goal: every web session boots with a working Python toolchain so the agent can
# immediately run the verification gate (`make check` = ruff + mypy + pytest)
# without first discovering and installing dependencies.
#
# Synchronous: the session waits for this to finish, which guarantees deps are
# ready before the agent runs anything. Progress goes to stderr to keep the
# agent's context (stdout) clean.
set -euo pipefail

# Only run in the remote web environment; local sessions manage their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

log() { echo "[session-start] $*" >&2; }

# 1. Install the Python project + dev tools (ruff / mypy / pytest). Idempotent;
#    `uv sync` is a no-op when the lockfile and venv are already in sync, and the
#    container caches the result after the first run.
log "uv sync --group dev"
uv sync --group dev >&2

# 2. Point git at the tracked hooks so the pre-push gate runs for this session.
#    Safe to re-run; just rewrites a single config value.
if [ -d .githooks ]; then
  log "git config core.hooksPath .githooks"
  git config core.hooksPath .githooks
fi

log "ready — run 'make check' to verify (ruff + mypy + pytest)"
