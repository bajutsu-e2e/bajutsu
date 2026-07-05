#!/usr/bin/env bash
# Launch the bajutsu web UI, installing the idb backend's dependencies on demand.
#
# The idb actuator needs two pieces the base install doesn't pull in — the `idb` python client and
# the `idb_companion` companion — and a missing one only surfaces at run time as `no available
# actuator among ['idb']`. Provisioning is delegated to the shared, config-aware installer
# (scripts/install.sh, BE-0164) forced to the idb backend, so "what idb needs" lives in one
# requirements mapping instead of being re-hardcoded here. Idempotent: nothing already present is
# reinstalled.
#
# Usage: scripts/serve.sh [bajutsu serve flags…]   e.g. scripts/serve.sh --port 8766
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/install.sh --backend idb

exec uv run python -m bajutsu serve "$@"
