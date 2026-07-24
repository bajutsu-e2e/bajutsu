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
# `testRunner`. This runs only when the serve actually drives the XCUITest backend (the no-config
# iOS default, or a config with an iOS target) — a web-only serve stays free of the toolchain build.
# Set BAJUTSU_SKIP_RUNNER_BUNDLE=1 to skip it regardless.
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

# True when this serve will drive the iOS/XCUITest backend, so the runner staging below runs only
# when it is actually wanted. With no local config, serve falls back to `--backend ios` (the branch
# at the bottom), so the default is iOS; a passed config is resolved through the same backend
# resolver the installer uses, keeping "which backend" in one place rather than re-parsed here.
serve_uses_xcuitest() {
  if [ -z "$config" ] || [ ! -f "$config" ]; then
    return 0
  fi
  uv run python - "$config" <<'PY'
import sys

from bajutsu.backends import resolve_actuators
from bajutsu.config import load_config, resolve

try:
    cfg = load_config(open(sys.argv[1], encoding="utf-8").read())
    actuators = {a for name in cfg.targets for a in resolve_actuators(resolve(cfg, name).backend)}
except Exception:
    # A malformed config is install.sh's problem to report below, not this probe's; treat it as
    # "no XCUITest" so a parse error never triggers a runner build.
    raise SystemExit(1)
raise SystemExit(0 if "xcuitest" in actuators else 1)
PY
}

# Stage the bundled XCUITest Simulator runner so `make serve` on a Mac makes XCUITest work out of
# the box (BE-0292). The bundled products live under `bajutsu/_xcuitest_runner/`, populated by `make
# runner-bundle` (an `xcodebuild build-for-testing`); a source checkout ships none, and without them
# the environment cannot fall back to the bundled runner. Build when absent OR stale — a warm,
# current bundle is reused, so this pays `xcodebuild` only on the first XCUITest serve of a fresh
# clone and again whenever a runner-affecting source changes; without the staleness half, a bundle
# built before a runner change (e.g. a new Router.swift endpoint) would silently keep serving that
# change's scenarios with the old runner. A build failure only warns, leaving serve to start for
# other backends.
runner_bundle="bajutsu/_xcuitest_runner/BajutsuRunner.xctestrun"
runner_build_info="bajutsu/_xcuitest_runner/build-info.json"

runner_bundle_stale() {
  [ ! -f "$runner_bundle" ] && return 0
  [ ! -f "$runner_build_info" ] && return 0
  recorded="$(sed -n 's/.*"sourceHash": *"\([^"]*\)".*/\1/p' "$runner_build_info")"
  [ -z "$recorded" ] && return 0
  [ "$recorded" != "$(./scripts/xcuitest-runner-hash.sh)" ]
}

if [ "${BAJUTSU_SKIP_RUNNER_BUNDLE:-}" != "1" ] &&
  [ "$(uname)" = "Darwin" ] &&
  runner_bundle_stale &&
  serve_uses_xcuitest; then
  if command -v xcodebuild >/dev/null 2>&1 && command -v xcodegen >/dev/null 2>&1; then
    echo "serve: staging bundled XCUITest runner (make runner-bundle)…" >&2
    if ! make runner-bundle; then
      echo "serve: WARNING: could not build the bundled XCUITest runner; XCUITest targets will need an explicit xcuitest.testRunner" >&2
    fi
  else
    # Name the specific missing tool with its own remedy: `make deps` installs xcodegen (via brew
    # bundle) but never Xcode, so conflating the two would misdirect whoever is missing xcodebuild.
    missing=""
    command -v xcodebuild >/dev/null 2>&1 || missing="Xcode (xcodebuild) — install Xcode"
    command -v xcodegen >/dev/null 2>&1 || missing="${missing:+$missing; }xcodegen — run 'make deps'"
    echo "serve: skipping bundled XCUITest runner staging — $missing" >&2
  fi
fi

if [ -n "$config" ] && [ -f "$config" ]; then
  ./scripts/install.sh --config "$config"
else
  ./scripts/install.sh --backend ios
fi

exec uv run python -m bajutsu serve "$@"
