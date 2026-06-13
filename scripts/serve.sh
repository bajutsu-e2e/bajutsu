#!/usr/bin/env bash
# Launch the bajutsu web UI, installing the idb backend's dependencies on demand.
#
# The idb actuator needs two pieces that the base install doesn't pull in, and a
# missing one only surfaces at run time as `no available actuator among ['idb']`:
#   - the `idb` python client  — uv extra `idb` (puts `idb` on the venv PATH)
#   - `idb_companion`          — a brew formula that drives the Simulator
# Both checks are idempotent: nothing is reinstalled once it is already present.
#
# Usage: scripts/serve.sh [bajutsu serve flags…]   e.g. scripts/serve.sh --port 8766
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. idb python client — the `idb` executable select_actuator() looks for on PATH.
#    `.venv/bin/idb` is exactly what uv puts on PATH for the spawned `run` subprocess.
if [ ! -x .venv/bin/idb ]; then
  echo "bajutsu: idb client missing — installing (uv sync --extra idb)…" >&2
  uv sync --extra idb
fi

# 2. idb_companion — a Homebrew formula (Brewfile). Auto-install only when brew exists.
if ! command -v idb_companion >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "bajutsu: idb_companion missing — installing (brew bundle)…" >&2
    brew bundle --file=Brewfile
  else
    echo "bajutsu: idb_companion is missing and Homebrew isn't available." >&2
    echo "         Install it manually: brew install facebook/fb/idb-companion" >&2
  fi
fi

exec uv run python -m bajutsu serve "$@"
