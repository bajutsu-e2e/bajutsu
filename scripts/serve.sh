#!/usr/bin/env bash
# Launch the bajutsu web UI, installing the configured backends' dependencies on demand.
#
# A backend's extra dependencies aren't in the base install, and a missing one only surfaces at run
# time (e.g. `no available actuator among ['xcuitest']`, or `ModuleNotFoundError: playwright`).
# Provisioning is delegated to the shared, config-aware installer (scripts/install.sh, BE-0164),
# which resolves exactly what a config's `targets.*` need, so "what a backend needs" lives in one
# requirements mapping instead of being re-hardcoded here. Idempotent: nothing already present is
# reinstalled.
#
# When a local `--config <file>` is passed, provision that config's actual backends — iOS (XCUITest),
# web (Playwright), or both — so a web-target UI doesn't get an iOS-only sync that prunes Playwright
# (and vice versa). Otherwise (no config, or a `github:`/`https:` spec install.sh can't resolve to
# a local file) fall back to the iOS backend: serve's historical iOS-first default.
#
# Usage: scripts/serve.sh [bajutsu serve flags…]   e.g. scripts/serve.sh --config demos/web/demo.config.yaml
set -euo pipefail

cd "$(dirname "$0")/.."

# Extract the value of a --config flag (both `--config X` and `--config=X`), without disturbing the
# original argv forwarded to `serve` verbatim below.
config=""
prev=""
for arg in "$@"; do
  case "$arg" in
    --config=*) config="${arg#--config=}" ;;
    *) [ "$prev" = "--config" ] && config="$arg" ;;
  esac
  prev="$arg"
done

if [ -n "$config" ] && [ -f "$config" ]; then
  ./scripts/install.sh --config "$config"
else
  ./scripts/install.sh --backend ios
fi

exec uv run python -m bajutsu serve "$@"
