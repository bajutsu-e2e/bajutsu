#!/bin/bash
# SessionStart hook for Claude Code.
#
# Two independent jobs:
#   1. (every session) Ensure the official `pr-review-toolkit` plugin is installed.
#      The implement-be skill's step-6 review pass launches its agents. The install
#      is idempotent (a no-op once present) and best-effort, so it never blocks or
#      breaks a session — a fresh clone gets the plugin without a manual step.
#   2. (web only) Boot a working Python toolchain so the agent can run the gate
#      (`make check` = ruff + mypy + pytest) immediately, without first discovering
#      and installing dependencies.
#
# Progress goes to stderr to keep the agent's context (stdout) clean.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

log() { echo "[session-start] $*" >&2; }

# 1. Ensure the review plugin is installed (user scope; does not touch the repo).
#    .claude/settings.json `enabledPlugins` only *enables* an already-installed plugin
#    — it does not install a missing one (anthropics/claude-code#23737), and references
#    to an uninstalled plugin are silently ignored (#32607), so a fresh clone needs this.
#    Idempotent and best-effort: a failure must never break the session, hence `|| log`.
#    This becomes redundant once enabledPlugins learns to auto-install (#23737).
if command -v claude >/dev/null 2>&1; then
  log "ensuring pr-review-toolkit@claude-plugins-official is installed"
  claude plugin install pr-review-toolkit@claude-plugins-official </dev/null >&2 \
    || log "pr-review-toolkit install skipped (non-fatal)"
fi

# 2. The Python toolchain bootstrap is web-specific; local sessions manage their venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Install the Python project + dev tools (ruff / mypy / pytest). Idempotent;
# `uv sync` is a no-op when the lockfile and venv are already in sync, and the
# container caches the result after the first run.
log "uv sync --group dev"
uv sync --group dev >&2

# Point git at the tracked hooks so the pre-push gate runs for this session.
# Safe to re-run; just rewrites a single config value.
if [ -d .githooks ]; then
  log "git config core.hooksPath .githooks"
  git config core.hooksPath .githooks
fi

log "ready — run 'make check' to verify (ruff + mypy + pytest)"
