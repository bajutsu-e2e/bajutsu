#!/usr/bin/env bash
#
# A guided lifecycle demo against the bundled `sample2` app:
#   1. GENERATE a scenario from a natural-language goal
#   2. EXECUTE it on a booted Simulator (idb) — a real pass
#   3. MODIFY the expectation and re-run — watch the check fail, then fix it
#
# Prereqs: a booted Simulator, the idb client, and the sample2 app built
# (`make -C demos/record sample2-build`). See demos/record/README.md.
#
#   ./demos/record/demo.sh
#
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root (the run's working directory)
HERE="demos/record"
CONFIG="$HERE/demo.config.yaml"
SCENARIO="$HERE/generated.yaml"
APP_PATH="app/sample2/build/dd/Build/Products/Debug-iphonesimulator/BajutsuSample.app"
GOAL="Get started, log in with email demo@bajutsu.dev and password hunter2, \
wait for Home, then tap Increment twice, and check the counter shows 2"

note() { printf '\n\033[1;36m== %s ==\033[0m\n' "$1"; }

run_scenario() {  # run the scenario; returns bajutsu's exit code (0 pass, 1 fail)
  uv run bajutsu run "$SCENARIO" --app sample2 --config "$CONFIG" --backend idb --no-network
}

set_expected_count() {  # $1 = new expected counter value — edit the generated scenario in place
  uv run python - "$SCENARIO" "$1" <<'PY'
import sys
from bajutsu.scenario import dump_scenario_file, load_scenario_file
path, value = sys.argv[1], sys.argv[2]
sf = load_scenario_file(open(path, encoding="utf-8").read())
for scenario in sf.scenarios:
    for assertion in scenario.expect:
        if assertion.value is not None:
            assertion.value.equals = value
open(path, "w", encoding="utf-8").write(dump_scenario_file(sf.scenarios, sf.description))
print(f"  edited {path}: expect counter value == {value!r}")
PY
}

# --- prerequisites -----------------------------------------------------------
note "Checking prerequisites"
command -v idb >/dev/null 2>&1 \
  || { echo "idb not found. Install: brew install facebook/fb/idb-companion && uv sync --extra idb"; exit 1; }
xcrun simctl list devices booted | grep -q "(Booted)" \
  || { echo "No booted Simulator. Boot one (open -a Simulator), then retry."; exit 1; }
[ -d "$APP_PATH" ] \
  || { echo "Sample app not built at $APP_PATH. Build it first: make -C demos/record sample2-build"; exit 1; }
echo "ok: idb, a booted Simulator, and the built sample2 app are present."

# --- 1) GENERATE -------------------------------------------------------------
note "1/3  Generate a scenario from a natural-language goal"
echo "goal: $GOAL"
uv run python "$HERE/generate_from_nl.py" "$GOAL" \
  --out "$SCENARIO" --name "counter smoke (generated from NL)" >/dev/null
echo "--- $SCENARIO ---"
cat "$SCENARIO"
echo "(For live Claude authoring against the running app instead, run:"
echo "   uv run bajutsu record $SCENARIO --app sample2 --config $CONFIG --backend idb \\"
echo "     --goal \"increment the counter twice and check it reads 2\")"

# --- 2) EXECUTE --------------------------------------------------------------
note "2/3  Run the generated scenario on the sample2 app (idb) — expect PASS"
run_scenario

# --- 3) MODIFY ---------------------------------------------------------------
note "3/3  Modify the scenario, then re-run"
echo "Change the expected count to a WRONG value (3) — the run should now FAIL,"
echo "because the app actually shows 2. This is the deterministic check doing its job:"
set_expected_count 3
if run_scenario; then
  echo "!! unexpected PASS — the app did not show 2?"; exit 1
else
  echo "-> FAIL as expected: the assertion caught the mismatch."
fi

echo
echo "Now fix it back to the correct value (2) and re-run — expect PASS again:"
set_expected_count 2
run_scenario

note "Done — you generated, executed, and modified a scenario against the sample2 app."
echo "The generated scenario is at $SCENARIO (gitignored). Edit the goal or the YAML and re-run."
