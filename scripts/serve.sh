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
# checkout ships none or has drifted from it
# (docs/specs/xcuitest-bundled-runner-auto-refresh.md), so a serve-launched XCUITest run resolves to
# a current one with no per-target `testRunner`. This runs only when the serve actually drives the
# XCUITest backend (the no-config iOS default, or a config with an iOS target) — a web-only serve
# stays free of the toolchain build. Set BAJUTSU_SKIP_RUNNER_BUNDLE=1 to skip it regardless.
#
# The server queues every run for a worker and executes none itself (BE-0449), so this script also
# starts a local `bajutsu worker` beside it, polling the server on its loopback address with the same
# token, from a temporary workspace this script removes when it exits. A run names the config,
# runs, and baselines by their paths on this machine, so the worker reads and writes the same ones
# the server does. The worker stops when serve
# stops. Set SERVE_WORKER=0 to start the server alone (to attach workers of your own), and
# `--emit-launchagent` starts nothing.
#
# Usage: scripts/serve.sh [bajutsu serve flags…]   e.g. scripts/serve.sh --config demos/web/demo.config.yaml
set -euo pipefail

cd "$(dirname "$0")/.."

# Extract the values of the flags this script reads (both `--flag X` and `--flag=X`), without
# disturbing the original argv forwarded to `serve` verbatim below. The worker needs the bind and the
# token; the provisioning below needs the config.
config=""
port="8765"
host="127.0.0.1"
token="${BAJUTSU_SERVE_TOKEN-}"
emit_launchagent=0
prev=""
for arg in "$@"; do
  case "$arg" in
    --config=*) config="${arg#--config=}" ;;
    --port=*) port="${arg#--port=}" ;;
    --host=*) host="${arg#--host=}" ;;
    --token=*) token="${arg#--token=}" ;;
    --emit-launchagent) emit_launchagent=1 ;;
    *)
      case "$prev" in
        --config) config="$arg" ;;
        --port) port="$arg" ;;
        --host) host="$arg" ;;
        --token) token="$arg" ;;
      esac
      ;;
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

from bajutsu.common.backends import resolve_actuators
from bajutsu.common.config import load_config, resolve

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
# the box (BE-0292), and keeps working as BajutsuKit's own source changes
# (docs/specs/xcuitest-bundled-runner-auto-refresh.md). Delegates the staleness check and rebuild to
# `ensure_bundled_runner_fresh` — the same function every other xcuitest entry point calls through
# `_resolve_runner` — so the algorithm lives in one place instead of a second, bash copy that could
# drift from it. That function itself no-ops on a wheel install (no BajutsuKit source to compare
# against) and honors BAJUTSU_SKIP_RUNNER_BUNDLE=1; a stale or missing bundle it cannot rebuild
# (Xcode/xcodegen missing, or the build itself failing) raises, which aborts serve before it starts —
# the same "never keep a stale bundle" contract every other entry point gets, rather than the silent
# per-target testRunner fallback this script used to degrade to.
if [ "${BAJUTSU_SKIP_RUNNER_BUNDLE:-}" != "1" ] &&
  [ "$(uname)" = "Darwin" ] &&
  serve_uses_xcuitest; then
  echo "serve: checking the bundled XCUITest runner…" >&2
  uv run python -c '
import sys

from bajutsu.common.backend_cli.simctl import DeviceError
from bajutsu.common.platform_lifecycle.environments._bundled_runner import (
    ensure_bundled_runner_fresh,
)

try:
    ensure_bundled_runner_fresh()
except DeviceError as exc:
    print(f"serve: {exc}", file=sys.stderr)
    sys.exit(1)
'
fi

if [ -n "$config" ] && [ -f "$config" ]; then
  ./scripts/install.sh --config "$config"
else
  ./scripts/install.sh --backend ios
fi

if [ "${SERVE_WORKER:-1}" = "0" ] || [ "$emit_launchagent" = "1" ]; then
  exec uv run python -m bajutsu serve "$@"
fi

# The interpreter of the provisioned environment, resolved once, so the worker starts from its own
# workspace without a second `uv run` racing the server's over the same environment.
python_bin="$(uv run python -c 'import sys; print(sys.executable)')"

# A worker reaches a wildcard bind over loopback, as the server's own signed URLs do.
case "$host" in
  "" | 0.0.0.0 | :: | "[::]") worker_host="127.0.0.1" ;;
  *) worker_host="$host" ;;
esac
case "$worker_host" in
  \[*) url_host="$worker_host" ;;
  *:*) url_host="[$worker_host]" ;;
  *) url_host="$worker_host" ;;
esac
# The platforms the bound config's targets need, from the same helper as the server's startup note,
# so the two never disagree. With no local config it falls back to what this machine can drive: the
# iOS Simulator only on macOS. A run for a platform left out stays queued with the no-worker notice.
platforms="$("$python_bin" -c '
import sys
from pathlib import Path

from bajutsu.serve import worker_platforms

path = Path(sys.argv[1]) if sys.argv[1] else None
print(worker_platforms(path if path is not None and path.is_file() else None))
' "$config" 2>/dev/null)" || platforms=""
if [ -z "$platforms" ]; then
  case "$(uname -s)" in
    Darwin) platforms="ios,web,android" ;;
    *) platforms="web,android" ;;
  esac
fi

tcp_host="${worker_host#[}"
tcp_host="${tcp_host%]}"

# How long the worker may take to stop: a leased run gets its cancellation grace window to write its
# report, then the worker posts the cancelled result. Past this bound the worker, and the run it
# recorded in its workspace, are killed.
grace="${BAJUTSU_CANCEL_GRACE:-90}"
case "$grace" in
  "" | *[!0-9.]* | .* | *.*.*)
    echo "serve: BAJUTSU_CANCEL_GRACE=$grace is not a number of seconds; waiting 90s for the worker" >&2
    grace=90
    ;;
esac
worker_stop_seconds=$((${grace%%.*} + 30))

# The worker's workspace. The owner file names this script and, once it starts, the worker: a worker
# started later removes the directory once every process named there has exited
# (`bajutsu worker`'s stale-workspace sweep), so killing this script alone never frees a live one.
worker_dir="$(mktemp -d "${TMPDIR:-/tmp}/bajutsu-worker-XXXXXX")"
echo "$$" >"$worker_dir/.owner-pid"

# Installed before either process starts, so a signal at any point stops what has started. The
# server runs in the background too, so a signal to this script is handled at once: bash runs a
# trap only after its foreground child returns, but `wait` returns as soon as a signal arrives.
# Background children ignore a terminal's Ctrl-C here, so the traps stop both explicitly.
worker_pid=""
serve_pid=""
# shellcheck disable=SC2329  # invoked by the EXIT trap below, which shellcheck does not follow
stop_all() {
  # The worker stops first, while the server can still take the cancelled result of its run;
  # otherwise the queue would re-run that job once the lease expired.
  if [ -n "$worker_pid" ]; then
    kill "$worker_pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$worker_pid" 2>/dev/null && [ "$waited" -lt $((worker_stop_seconds * 2)) ]; do
      sleep 0.5
      waited=$((waited + 1))
    done
    if kill -0 "$worker_pid" 2>/dev/null; then
      kill -KILL "$worker_pid" 2>/dev/null || true
      # The run lives in a session of its own; end it too, through the group the worker recorded.
      local pgid
      pgid="$(cat "$worker_dir/.job-pgid" 2>/dev/null || true)"
      case "$pgid" in
        "" | *[!0-9]*) ;;
        *) kill -KILL -- "-$pgid" 2>/dev/null || true ;;
      esac
    fi
    wait "$worker_pid" 2>/dev/null || true
  fi
  if [ -n "$serve_pid" ]; then
    kill "$serve_pid" 2>/dev/null || true
    wait "$serve_pid" 2>/dev/null || true
  fi
  rm -rf "$worker_dir"
}
trap stop_all EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

(
  # Wait for the server to accept connections, so the worker's first polls do not log refusals.
  until (exec 3<>"/dev/tcp/$tcp_host/$port") 2>/dev/null; do
    sleep 0.5
  done
  # The token travels in the environment, not argv, so it stays out of the process list.
  BAJUTSU_TOKEN="$token" exec "$python_bin" -m bajutsu worker \
    --server-url "http://$url_host:$port" --platform "$platforms" --workdir "$worker_dir"
) &
worker_pid=$!
echo "$worker_pid" >>"$worker_dir/.owner-pid"

echo "serve: started a local worker (pid $worker_pid); set SERVE_WORKER=0 to start the server alone" >&2
uv run python -m bajutsu serve "$@" &
serve_pid=$!
status=0
wait "$serve_pid" || status=$?
serve_pid=""
exit "$status"
