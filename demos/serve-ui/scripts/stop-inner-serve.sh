#!/bin/sh
# Stop the inner `serve` this run started, the `hooks.afterRun` half of the pair (BE-0491).
#
# The start script hands the process id file over through $BAJUTSU_OUTPUT, which reaches here as
# $BAJUTSU_HOOK__server__pidFile. An empty value means the start script found a server already
# answering and reused it: that one belongs to whoever started it, so this script leaves it alone.
set -eu

PID_FILE="${BAJUTSU_HOOK__server__pidFile:-}"
[ -n "$PID_FILE" ] || exit 0
[ -f "$PID_FILE" ] || exit 0

PID="$(cat "$PID_FILE")"
rm -f "$PID_FILE"
kill "$PID" 2>/dev/null || exit 0
# Give it a moment to go down on its own before insisting, so it can run its own shutdown.
i=0
while kill -0 "$PID" 2>/dev/null && [ "$i" -lt 20 ]; do
    sleep 0.25
    i=$((i + 1))
done
kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
exit 0
