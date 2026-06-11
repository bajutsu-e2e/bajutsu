#!/usr/bin/env bash
#
# A guided lifecycle demo against the bundled `sample2` app:
#   1. AUTHOR a scenario from a natural-language goal — Claude (`bajutsu record`)
#      reads the live app and proposes each step (streamed live: 💭 reasoning → action)
#   2. EXECUTE it on a booted Simulator (idb) — a real pass
#   3. MODIFY the expectation and re-run — watch the check fail, then fix it
#   4. DIAGNOSE — break a selector, let `triage` diagnose the failure (advisory)
#
# Authoring uses the Claude Code agent (`--agent claude-code`), so it draws on your Claude
# subscription instead of API credits. Override with `AGENT=api ./demo.sh`.
#
# Prereqs: a booted Simulator, the idb client, the sample2 app built
# (`make -C demos/record sample2-build`), the `claude` CLI logged in (for claude-code), and
# ANTHROPIC_API_KEY (env or .env) — still needed for the login "Save Password" alert guard.
# See demos/record/README.md.
#
#   ./demos/record/demo.sh
#
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root (the run's working directory)
HERE="demos/record"
CONFIG="$HERE/demo.config.yaml"
SCENARIO="$HERE/generated.yaml"
APP_PATH="demos/record/app/build/dd/Build/Products/Debug-iphonesimulator/BajutsuSample.app"
GOALS_FILE="$HERE/goals.txt"
AGENT="${AGENT:-claude-code}"   # authoring agent: claude-code (subscription) or api (Anthropic API)
# The goal is the first non-comment, non-blank line of goals.txt (the counter-shows-2 flow
# steps 3/4 depend on). Edit that line — or override with `GOAL="..." ./demo.sh`.
GOAL="${GOAL:-$(grep -vE '^[[:space:]]*(#|$)' "$GOALS_FILE" | head -n1)}"
[ -n "$GOAL" ] || { echo "No goal found in $GOALS_FILE (all lines blank or commented)."; exit 1; }

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
# The counter check is `value.equals` on an id/value-bearing app, or `label.contains` on the
# label-only sample2 (e.g. asserting the "Count: 2" text). Edit whichever the agent produced.
n = 0
for scenario in sf.scenarios:
    for assertion in scenario.expect:
        if assertion.value is not None:
            assertion.value.equals = value
            n += 1
        elif assertion.label is not None:
            assertion.label.contains = value
            n += 1
open(path, "w", encoding="utf-8").write(dump_scenario_file(sf.scenarios, sf.description))
print(f"  edited {path}: expect counter == {value!r} ({n} assertion(s))")
PY
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
  echo "Sample app not built at $APP_PATH — building it now (make -C demos/record sample2-build)..."
  make -C demos/record sample2-build
  [ -d "$APP_PATH" ] \
    || { echo "Build finished but $APP_PATH is still missing."; exit 1; }
fi
if [ "$AGENT" = "claude-code" ]; then
  command -v claude >/dev/null 2>&1 \
    || { echo "claude CLI not found (needed for --agent claude-code). Install Claude Code, or run with AGENT=api."; exit 1; }
fi
# The API key is still needed for the login "Save Password" alert guard (vision), even when
# authoring runs on the claude-code agent.
[ -n "${ANTHROPIC_API_KEY:-}" ] || grep -q '^[[:space:]]*ANTHROPIC_API_KEY=' .env 2>/dev/null \
  || { echo "ANTHROPIC_API_KEY not set — needed for the alert guard. Export it or add it to .env."; exit 1; }
echo "ok: idb, a booted Simulator, the built sample2 app, the $AGENT agent, and an API key are present."

# --- 1) AUTHOR (AI) ----------------------------------------------------------
note "1/4  Author a scenario from a natural-language goal — Claude reads the live app"
echo "goal: $GOAL"
rm -f "$SCENARIO"   # always start from scratch — discard any scenario left by a prior run
# `bajutsu record` runs the real Tier-1 authoring loop: the agent reads the goal + a
# screenshot + the accessibility tree on the booted sample2 app and proposes one step at a
# time (streamed live: 💭 reasoning → action); the loop writes the executed steps out as the
# deterministic scenario `run` replays. The claude-code agent bills your Claude subscription.
uv run bajutsu record "$SCENARIO" --app sample2 --goal "$GOAL" \
  --config "$CONFIG" --backend idb --no-erase --agent "$AGENT"
echo "--- $SCENARIO ---"
cat "$SCENARIO"
echo "(Offline, no API key / Simulator? The keyword stand-in authors the same flow:"
echo "   uv run python $HERE/generate_from_nl.py \"\$GOAL\" --out $SCENARIO)"

# --- 2) EXECUTE --------------------------------------------------------------
note "2/4  Run the generated scenario on the sample2 app (idb) — expect PASS"
if run_scenario; then
  echo "-> PASS."
else
  echo "-> FAIL. Don't stop — run triage to diagnose and self-heal, then carry on:"
  uv run bajutsu triage --apply "$SCENARIO" --write --rerun \
    --app sample2 --backend idb --config "$CONFIG" || true
fi

# --- 3) MODIFY ---------------------------------------------------------------
note "3/4  Modify the scenario, then re-run"
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

# --- 4) DIAGNOSE -------------------------------------------------------------
note "4/4  Break a selector, then let triage diagnose the failure"
echo "Change the 'Log in' button's label to 'Log In' in the scenario, simulating a selector"
echo "that drifted out from under the test. Re-run — the tap can't resolve its target, so it FAILS:"
replace_in_scenario "label: Log in" "label: Log In"
if run_scenario; then
  echo "!! unexpected PASS — the broken selector still resolved?"; exit 1
else
  echo "-> FAIL as expected: the selector no longer matches an element."
fi

echo
echo "Diagnose the failed run with triage (advisory — it explains the failure and points at the"
echo "likely fix from the captured element tree, but never judges pass/fail):"
uv run bajutsu triage --app sample2 --config "$CONFIG" || true

echo
echo "Restore the selector and re-run — expect PASS again:"
replace_in_scenario "label: Log In" "label: Log in"
run_scenario

note "Done — you authored, executed, modified, and diagnosed a scenario against the sample2 app."
echo "The generated scenario is at $SCENARIO (gitignored). Edit the goal or the YAML and re-run."
