#!/usr/bin/env bash
#
# A guided, fully deterministic lifecycle demo — no AI, no API key — against the showcase
# SwiftUI app (demos/showcase/ios/swiftui/) on a booted Simulator via XCUITest:
#   1. RUN      the committed scenario on the Simulator — a real PASS
#   2. MODIFY   break the expected value, re-run — watch the check FAIL, then fix it
#   3. DIAGNOSE break a selector, re-run -> FAIL, let `triage` diagnose it (advisory)
#
# This is the on-device version of the zero-setup tour (demos/tour/tour.py): the same
# author -> run -> modify -> diagnose story, but on a real device and a real app, and with
# the scenario already authored (so no Claude / API key is needed — `run` and `triage`
# never use AI). See demos/tour/README.md.
#
# Prereqs: Xcode, a booted Simulator (`open -a Simulator`), the XCUITest runner
# (built on demand below via `make -C demos/showcase runner-build`), and the showcase app built
# (built on demand below via `make -C demos/showcase swiftui-build`).
#
#   ./demos/tour/demo.sh        (or: make -C demos tour)
#
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root (the run's working directory)
CONFIG="demos/demo.config.yaml"
SOURCE="demos/showcase/scenarios/menu/tour.yaml"
SCENARIO="demos/tour/scenario.yaml"   # gitignored working copy — we edit this, not the tracked source
APP_PATH="demos/showcase/ios/swiftui/build/dd/Build/Products/Debug-iphonesimulator/BajutsuShowcaseSwiftUI.app"

note() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

run_scenario() {  # run the working scenario on the Simulator; returns bajutsu's exit code
  uv run bajutsu run --scenario "$SCENARIO" --target showcase-swiftui --config "$CONFIG" --backend ios --no-network
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
RUNNER="BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun"
command -v xcodebuild >/dev/null 2>&1 \
  || { echo "xcodebuild not found. Install Xcode (the XCUITest backend needs it)."; exit 1; }
xcrun simctl list devices booted | grep -q "(Booted)" \
  || { echo "No booted Simulator. Boot one (open -a Simulator), then retry."; exit 1; }
if [ ! -f "$RUNNER" ]; then
  echo "XCUITest runner not built at $RUNNER — building it now (make -C demos/showcase runner-build)..."
  make -C demos/showcase runner-build
  [ -f "$RUNNER" ] || { echo "Build finished but $RUNNER is still missing."; exit 1; }
fi
if [ ! -d "$APP_PATH" ]; then
  echo "Showcase app not built at $APP_PATH — building it now (make -C demos/showcase swiftui-build)..."
  make -C demos/showcase swiftui-build
  [ -d "$APP_PATH" ] || { echo "Build finished but $APP_PATH is still missing."; exit 1; }
fi
echo "ok: Xcode, a booted Simulator, the XCUITest runner, and the built showcase app are present."

# Seed a fresh working copy from the committed scenario so the tracked file stays clean.
cp "$SOURCE" "$SCENARIO"

# --- 1) RUN ------------------------------------------------------------------
note "1/3  Run the committed scenario on the showcase app (XCUITest) — expect PASS"
if run_scenario; then
  echo "-> PASS."
else
  echo "-> FAIL. Don't stop — run triage to diagnose and self-heal, then carry on:"
  uv run bajutsu triage --apply "$SCENARIO" --write --rerun \
    --target showcase-swiftui --backend ios --config "$CONFIG" || true
fi

# --- 2) MODIFY ---------------------------------------------------------------
note "2/3  Modify the scenario, then re-run"
echo "Change the expected favorite state to a WRONG value (off) — the run should now FAIL, because"
echo "the toggle is actually on. This is the deterministic check doing its job (no AI judged it):"
replace_in_scenario 'equals: "on"' 'equals: "off"'
if run_scenario; then
  echo "!! unexpected PASS — the favorite toggle did not read on?"; exit 1
else
  echo "-> FAIL as expected: the assertion caught the mismatch."
fi

echo
echo "Now fix it back to the correct value (on) and re-run — expect PASS again:"
replace_in_scenario 'equals: "off"' 'equals: "on"'
run_scenario

# --- 3) DIAGNOSE -------------------------------------------------------------
note "3/3  Break a selector, then let triage diagnose the failure"
echo "Rename the tapped Stable row's id (stable.row.3 -> stable.row.99), simulating a selector"
echo "that drifted out from under the test. Re-run — the wait can't resolve it, so it FAILS:"
replace_in_scenario 'id: stable.row.3' 'id: stable.row.99'
if run_scenario; then
  echo "!! unexpected PASS — the broken selector still resolved?"; exit 1
else
  echo "-> FAIL as expected: the selector no longer matches an element."
fi

echo
echo "Diagnose the failed run with triage (advisory — it points at the likely fix from the"
echo "captured element tree, but never judges pass/fail):"
uv run bajutsu triage --target showcase-swiftui --config "$CONFIG" || true

echo
echo "Restore the selector and re-run — expect PASS again:"
replace_in_scenario 'id: stable.row.99' 'id: stable.row.3'
run_scenario

note "Done — you ran, modified, and diagnosed a scenario on a real Simulator (no AI, no API key)"
echo "The working scenario is at $SCENARIO (gitignored); the committed source is $SOURCE."
echo "The zero-setup version of this story (no Mac/Simulator): uv run python demos/tour/tour.py"
