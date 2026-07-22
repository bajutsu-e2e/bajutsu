"""The whole Bajutsu story, with zero setup — no Simulator, no Xcode, no API key.

This is the same four-phase lifecycle as the on-device demo (`demos/record/demo.sh`),
run end to end against an in-memory `FakeDriver` so it works anywhere — Linux, CI, a fresh
clone — in a couple of seconds:

  1. AUTHOR   — a natural-language goal becomes a deterministic scenario (the real
                `record` loop, with a keyword stand-in for Claude so it needs no API key).
  2. EXECUTE  — `run` replays it through the real pipeline (`run_and_report`), writing a
                genuine run directory: manifest.json, JUnit XML, and a self-contained
                report.html you can open in a browser. It PASSES.
  3. MODIFY   — change the expected counter to a wrong value → the deterministic check
                FAILS (no AI judged it — a machine assertion did) → fix it → it PASSES.
  4. DIAGNOSE — rename a selector so it no longer resolves (a selector that drifted out
                from under the test) → the run FAILS → `triage` reads the failed run and
                diagnoses it (category + the "did you mean" fix from the captured element
                tree) → restore the selector → it PASSES.

Everything but the "brain" in phase 1 is the production code path: the same orchestrator,
the same assertion engine, the same report writer, the same heuristic triage. Run it:

    uv run python demos/tour/tour.py

The on-device version of this exact story — the same run -> modify -> diagnose on a real
Simulator, also deterministic and key-free — is `demos/tour/demo.sh` (`make -C demos tour`).
See `demos/README.md` for the map.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The keyword authoring stand-in + its mock app live with the record demo; reuse them so
# there is a single source of truth for the offline `record` brain.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "record"))

from generate_from_nl import DEFAULT_GOAL, author, make_app  # noqa: E402

from bajutsu import triage as _triage  # noqa: E402
from bajutsu.config import Effective, IosConfig  # noqa: E402
from bajutsu.evidence import FileSink  # noqa: E402
from bajutsu.runner import Lease, run_and_report  # noqa: E402
from bajutsu.scenario import (  # noqa: E402
    Redact,
    Scenario,
    dump_scenario_file,
    load_scenario_file,
)

RUNS = HERE / "runs"  # gitignored scratch (the repo's top-level runs/ is ignored)
SCENARIO = HERE / "generated.yaml"


def note(title: str) -> None:
    print(f"\n\033[1;36m== {title} ==\033[0m")


def _eff() -> Effective:
    """A minimal effective config for the FakeDriver-backed showcase flow."""
    return Effective(
        target="showcase-swiftui",
        platform_config=IosConfig(bundle_id="com.bajutsu.showcase.ios.swiftui"),
        backend=["fake"],
        device="FakeDriver",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        mailbox=None,
        setup=None,
        capture=["screenshot.after", "elements.after"],
        redact=Redact(),
    )


def run_scenarios(run_id: str) -> tuple[bool, Path]:
    """Replay the scenario file through the real run pipeline on a fresh FakeDriver.

    Each scenario leases its own freshly-scripted mock app (taps advance onboarding →
    login → home → counter), captures evidence into a real run dir, and writes a report.
    """

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        run_dir = RUNS / run_id
        return Lease(
            driver=make_app(),
            sink=FileSink(run_dir),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    sf = load_scenario_file(SCENARIO.read_text(encoding="utf-8"))
    results, manifest = run_and_report(
        _eff(), sf.scenarios, lease, RUNS, run_id, description=sf.description
    )
    ok = all(r.ok for r in results)
    print(f"  -> {'PASS' if ok else 'FAIL'}  ({manifest})")
    for r in results:
        if not r.ok:
            print(f"     {r.scenario}: {r.failure}")
    return ok, RUNS / run_id


def edit_scenario(find: str, replace: str) -> None:
    """A plain in-place text edit of the generated scenario — the maintenance loop."""
    text = SCENARIO.read_text(encoding="utf-8")
    n = text.count(find)
    SCENARIO.write_text(text.replace(find, replace), encoding="utf-8")
    print(f"  edited {SCENARIO.name}: {find!r} -> {replace!r} ({n} occurrence(s))")


def main() -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    goal = DEFAULT_GOAL

    # --- 1) AUTHOR -----------------------------------------------------------
    note("1/4  Author a scenario from a natural-language goal (the real record loop)")
    print(f"goal: {goal}")
    scenario = author(goal, name="counter")
    SCENARIO.write_text(
        dump_scenario_file([scenario], "Bajutsu tour — counter flow"), encoding="utf-8"
    )
    print(f"\nRecorded {len(scenario.steps)} steps as deterministic YAML -> {SCENARIO}:\n")
    print(SCENARIO.read_text(encoding="utf-8"))

    # --- 2) EXECUTE ----------------------------------------------------------
    note("2/4  Run the generated scenario (real pipeline + report) — expect PASS")
    ok, run_dir = run_scenarios("02-pass")
    if not ok:
        print("!! the freshly-authored scenario should pass — aborting.")
        return 1
    print(f"  open the report: {run_dir / 'report.html'}")

    # --- 3) MODIFY -----------------------------------------------------------
    note("3/4  Break the expected counter (2 -> 3), then re-run — expect FAIL")
    print("The app shows 2; asserting 3 must fail. No AI judged this — a machine assertion did:")
    edit_scenario("equals: '2'", "equals: '3'")
    ok, _ = run_scenarios("03-fail")
    if ok:
        print("!! unexpected PASS — the assertion did not catch the mismatch.")
        return 1
    print("  -> FAIL as expected: the deterministic check caught it.")
    print("\nFix it back to 2 and re-run — expect PASS again:")
    edit_scenario("equals: '3'", "equals: '2'")
    ok, _ = run_scenarios("03-fixed")
    if not ok:
        print("!! expected PASS after the fix.")
        return 1

    # --- 4) DIAGNOSE ---------------------------------------------------------
    note("4/4  Rename a selector so it no longer resolves, then let triage diagnose it")
    print("Simulate a selector that drifted: counter.increment -> counter.increments.")
    print("The tap can't resolve its target, so the run FAILS:")
    edit_scenario("id: counter.increment", "id: counter.increments")
    ok, run_dir = run_scenarios("04-broken")
    if ok:
        print("!! unexpected PASS — the broken selector still resolved?")
        return 1
    print("  -> FAIL as expected: the selector no longer matches an element.")

    print("\nDiagnose the failed run with triage (advisory — it never judges pass/fail):\n")
    context = _triage.assemble(run_dir)
    if context is None:
        print("!! triage found no failed scenario to diagnose.")
        return 1
    result = _triage.HeuristicTriageAgent().triage(context)
    print(_triage.render(context, result))

    print("\nRestore the selector and re-run — expect PASS again:")
    edit_scenario("id: counter.increments", "id: counter.increment")
    ok, _ = run_scenarios("04-fixed")
    if not ok:
        print("!! expected PASS after restoring the selector.")
        return 1

    note("Done — you authored, executed, modified, and diagnosed a scenario with zero setup")
    print(
        f"The generated scenario is at {SCENARIO} (gitignored). Edit the goal or YAML and re-run."
    )
    print("Run this same story on a real Simulator (deterministic, no API key): make -C demos tour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
