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
# On macOS it also stages the wheel-bundled XCUITest Simulator runner (BE-0292) when a source
# checkout ships none, so a serve-launched XCUITest run resolves to it with no per-target
# `testRunner`. Set BAJUTSU_SKIP_RUNNER_BUNDLE=1 to skip that step (e.g. a web-only Mac session).
#
# Usage: scripts/serve.sh [bajutsu serve flags…]   e.g. scripts/serve.sh --config demos/web/demo.config.yaml
set -euo pipefail

cd "$(dirname "$0")/.."

# Stage the bundled XCUITest Simulator runner so `make serve` on a Mac makes XCUITest work out of
# the box (BE-0292). The bundled products live under `bajutsu/_xcuitest_runner/`, populated by `make
# runner-bundle` (an `xcodebuild build-for-testing`); a source checkout ships none, and without them
# the environment cannot fall back to the bundled runner. Build once when absent — a warm bundle is
# reused, so this pays `xcodebuild` only on the first serve of a fresh clone. Guarded to macOS with
# Xcode + xcodegen present; a build failure only warns, leaving serve to start for other backends.
runner_bundle="bajutsu/_xcuitest_runner/BajutsuRunner.xctestrun"
if [ "${BAJUTSU_SKIP_RUNNER_BUNDLE:-}" != "1" ] && [ "$(uname)" = "Darwin" ] && [ ! -f "$runner_bundle" ]; then
  if command -v xcodebuild >/dev/null 2>&1 && command -v xcodegen >/dev/null 2>&1; then
    echo "serve: staging bundled XCUITest runner (make runner-bundle)…" >&2
    if ! make runner-bundle; then
      echo "serve: WARNING: could not build the bundled XCUITest runner; XCUITest targets will need an explicit xcuitest.testRunner" >&2
    fi
  else
    echo "serve: skipping bundled XCUITest runner staging — Xcode/xcodegen not found (run 'make deps')" >&2
  fi
fi

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
