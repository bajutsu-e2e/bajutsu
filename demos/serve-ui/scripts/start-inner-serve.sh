#!/bin/sh
# Bring the inner `serve` (the Web UI under test) up for the dogfood run, and exit once it answers.
#
# This is the `hooks.beforeRun` form of what `services.server` did before BE-0491 removed it. The
# readiness check lives here, where this server's own condition can be named — it answers HTTP, so
# the script polls the URL — and the entry's own `timeout` bounds the wait. The port the server
# listens on is published to the scenario through $BAJUTSU_OUTPUT, so the teardown script stops
# exactly the process this one started and nothing else.
set -eu

URL="${INNER_URL:-http://127.0.0.1:8799/}"
PORT="${INNER_PORT:-8799}"
PID_FILE="${TMPDIR:-/tmp}/bajutsu-dogfood-serve-${PORT}.pid"

answers() {
    curl -fsS -o /dev/null --max-time 2 "$URL" 2>/dev/null
}

# Something is already serving (a `make serve-ui` left running, or CI started it): reuse it
# untouched, and tell the teardown there is nothing of ours to stop.
if answers; then
    echo "pidFile=" >>"$BAJUTSU_OUTPUT"
    echo "reused=yes" >>"$BAJUTSU_OUTPUT"
    exit 0
fi

uv run bajutsu serve \
    --config demos/serve-ui/inner.config.yaml \
    --root demos/serve-ui \
    --runs demos/serve-ui/fixtures/crawl-runs \
    --port "$PORT" &
printf '%s\n' "$!" >"$PID_FILE"

# A condition wait, never a fixed sleep: poll until the server answers. The entry's `timeout` is
# what bounds it, so this loop does not need a deadline of its own.
while ! answers; do
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "inner serve exited before it answered $URL" >&2
        exit 1
    fi
    sleep 0.25
done

echo "pidFile=$PID_FILE" >>"$BAJUTSU_OUTPUT"
echo "reused=no" >>"$BAJUTSU_OUTPUT"
