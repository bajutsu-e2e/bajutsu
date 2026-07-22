#!/usr/bin/env bash
# Config-aware environment installer (BE-0164): install exactly the pip extras and external tools
# a project's configured backends need — not "every backend unconditionally", not "everything".
#
# The base Python toolchain + git hooks are `make setup`'s job (config-agnostic); this adds the
# backend layer on top. `make install` runs `setup` first, so a bare-clone `make install` gets
# both. Idempotent — safe to re-run: nothing already present is reinstalled.
#
# Usage: scripts/install.sh [--config <path>] [--backend <name>] [--dry-run]
#   scripts/install.sh                                  # ./bajutsu.config.yaml if present, else base only
#   scripts/install.sh --config demos/showcase/showcase.config.yaml
#   scripts/install.sh --backend ios                    # force the iOS backend (the `make deps` path)
set -euo pipefail

cd "$(dirname "$0")/.."

exec uv run python -m bajutsu.provision "$@"
