#!/usr/bin/env bash
#
# A guided, fully deterministic lifecycle demo — no AI, no API key — against the bundled
# demo app (demos/app/) on a booted Simulator via idb:
#   1. RUN      the committed scenario on the Simulator — a real PASS
#   2. MODIFY   break the expected count, re-run — watch the check FAIL, then fix it
#   3. DIAGNOSE break a selector, re-run -> FAIL, let `triage` diagnose it (advisory)
#
# This is the on-device version of the zero-setup tour (demos/tour/tour.py): the same
# author -> run -> modify -> diagnose story, but on a real device and a real app, and with
# the scenario already authored (so no Claude / API key is needed — `run` and `triage`
# never use AI). See demos/tour/README.md.
#
# Prereqs: a booted Simulator (`open -a Simulator`), the idb client
# (`brew install facebook/fb/idb-companion && uv sync --extra idb`), and the demo app built
# (built on demand below via `make -C demos app-build`).
#
#   ./demos/tour/demo.sh        (or: make -C demos tour)
#
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root (the run's working directory)
CONFIG="demos/demo.config.yaml"
SOURCE="demos/app/scenarios/counter.yaml"
SCENARIO="demos/tour/scenario.yaml"   # gitignored working copy — we edit this, not the tracked source
APP_PATH="demos/app/build/dd/Build/Products/Debug-iphonesimulator/BajutsuDemo.app"

note() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

run_scenario() {  # run the working scenario on the Simulator; returns bajutsu's exit code
  uv run bajutsu run --scenario "$SCENARIO" --app demo --config "$CONFIG" --backend ios --no-network
}

replace_in_scenario() {  # $1 = exact text to find, $2 = replacement — a plain in-place edit
  uv run python - "$SCENARIO" "$1" "$2" <<'PY'
import sys
path, find, repl = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path, encoding="utf-8").read()
n = text.count(find)
open(path, "w", encoding="utf-8").write(text.replace(find, repl))
print(f"  replaced {find!r} -> {repl!r} in {path} ({n} occurrence(s))")
PY
}

# --- prerequisites -----------------------------------------------------------
note "Checking prerequisites"
uv run idb --help >/dev/null 2>&1 \
  || { echo "idb not found. Install: brew install facebook/fb/idb-companion && uv sync --extra idb"; exit 1; }
xcrun simctl list devices booted | grep -q "(Booted)" \
  || { echo "No booted Simulator. Boot one (open -a Simulator), then retry."; exit 1; }
if [ ! -d "$APP_PATH" ]; then
  echo "Demo app not built at $APP_PATH — building it now (make -C demos app-build)..."
  make -C demos app-build
  [ -d "$APP_PATH" ] || { echo "Build finished but $APP_PATH is still missing."; exit 1; }
fi
echo "ok: idb, a booted Simulator, and the built demo app are present."

# Seed a fresh working copy from the committed scenario so the tracked file stays clean.
cp "$SOURCE" "$SCENARIO"

# --- 1) RUN ------------------------------------------------------------------
note "1/3  Run the committed scenario on the demo app (idb) — expect PASS"
if run_scenario; then
  echo "-> PASS."
else
  echo "-> FAIL. Don't stop — run triage to diagnose and self-heal, then carry on:"
  uv run bajutsu triage --apply "$SCENARIO" --write --rerun \
    --app demo --backend ios --config "$CONFIG" || true
fi

# --- 2) MODIFY ---------------------------------------------------------------
note "2/3  Modify the scenario, then re-run"
echo "Change the expected count to a WRONG value (3) — the run should now FAIL, because the"
echo "app actually shows 2. This is the deterministic check doing its job (no AI judged it):"
replace_in_scenario 'equals: "2"' 'equals: "3"'
if run_scenario; then
  echo "!! unexpected PASS — the app did not show 2?"; exit 1
else
  echo "-> FAIL as expected: the assertion caught the mismatch."
fi

echo
echo "Now fix it back to the correct value (2) and re-run — expect PASS again:"
replace_in_scenario 'equals: "3"' 'equals: "2"'
run_scenario

# --- 3) DIAGNOSE -------------------------------------------------------------
note "3/3  Break a selector, then let triage diagnose the failure"
echo "Rename the Increment button's id (counter.increment -> counter.increments), simulating a"
echo "selector that drifted out from under the test. Re-run — the tap can't resolve, so it FAILS:"
replace_in_scenario 'id: counter.increment' 'id: counter.increments'
if run_scenario; then
  echo "!! unexpected PASS — the broken selector still resolved?"; exit 1
else
  echo "-> FAIL as expected: the selector no longer matches an element."
fi

echo
echo "Diagnose the failed run with triage (advisory — it points at the likely fix from the"
echo "captured element tree, but never judges pass/fail):"
uv run bajutsu triage --app demo --config "$CONFIG" || true

echo
echo "Restore the selector and re-run — expect PASS again:"
replace_in_scenario 'id: counter.increments' 'id: counter.increment'
run_scenario

note "Done — you ran, modified, and diagnosed a scenario on a real Simulator (no AI, no API key)"
echo "The working scenario is at $SCENARIO (gitignored); the committed source is $SOURCE."
echo "The zero-setup version of this story (no Mac/Simulator): uv run python demos/tour/tour.py"
