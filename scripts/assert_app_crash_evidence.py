#!/usr/bin/env python3
"""Assert a real app crash was diagnosed and its platform report captured (BE-0424).

`app_crash.yaml` deliberately faults the showcase app mid-scenario, so the run it belongs to is
*expected* to fail. That makes a plain exit code useless as the check: a red run proves only that
something went wrong, which is exactly what the failure this item exists to fix already looked like.
This helper turns "the crash was diagnosed, and its evidence landed" into a pass/fail an E2E lane can
gate on, read from what the finished run actually left behind — the same shape
`demos/showcase/network/assert_network_evidence.py` and `assert_pool_isolation.py` use for their own
on-device claims.

The fast suite proves the classification and the write against stubbed directories and stubbed `adb`
output. What no unit test can show is that the *platform* behaves the way the design assumes: that
macOS's `ReportCrash` really writes the `.ips`, that `logcat`'s crash buffer really carries the
`FATAL EXCEPTION` block, and that `XCUIApplication.state` / `pidof` + `dumpsys activity exit-info`
really report the crash the detection keys off. That is the gap this script closes.

Three checks, each failing loudly rather than silently passing on a run that never crashed:

- **The scenario failed.** A green run means the fixture did not fault at all — the affordance
  compiled out, or the trigger id moved — which would otherwise pass as "no crash detected" and
  prove nothing.
- **Exactly one step outcome carries `app_crashed`.** The classification is designed so at most one
  outcome per scenario carries it; several would mean a wrapping outcome competed with the
  confirming one, which is the ambiguity the latch exists to prevent.
- **The `app-crash/` directory holds a non-empty report.** The point of the item is the evidence, not
  the label, so a classification with an empty directory behind it is a failure here.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_APP_CRASH_DIR = "app-crash"


def _outcomes(scenario: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Every step outcome the scenario recorded, across all three lifecycle phases.

    The crashed outcome is not always in `steps`: a `before` step's failure leaves `steps` empty
    outright, and an `after` rule dispatched on the failure records into its own list.
    """
    found: list[Mapping[str, object]] = []
    for phase in ("before_outcomes", "steps", "after_outcomes"):
        entries = scenario.get(phase)
        if isinstance(entries, list):
            found.extend(o for o in entries if isinstance(o, Mapping))
    return found


def violations(scenario: Mapping[str, object], run_dir: Path) -> list[str]:
    """Everything wrong with this scenario's app-crash evidence, empty when it is sound."""
    found: list[str] = []
    if scenario.get("ok") is not False:
        found.append(
            "the scenario passed, so the fixture never crashed the app — check that the "
            "SHOWCASE_CRASH affordance is compiled in and its trigger id still resolves"
        )
    crashed = [o for o in _outcomes(scenario) if o.get("app_crashed") is True]
    if not crashed:
        found.append(
            "no step outcome carries `app_crashed`: the app went down but the run did not "
            "diagnose it, which is the misdiagnosis BE-0424 exists to remove"
        )
    elif len(crashed) > 1:
        found.append(
            f"{len(crashed)} step outcomes carry `app_crashed`; at most one may, so "
            "`pipeline.py`'s scan never has to choose among several"
        )
    sid = scenario.get("sid")
    if not isinstance(sid, str) or not sid:
        found.append("the scenario records no `sid`, so its evidence directory cannot be located")
        return found
    crash_dir = run_dir / sid / _APP_CRASH_DIR
    if not crash_dir.is_dir():
        found.append(f"no {_APP_CRASH_DIR}/ directory at {crash_dir}")
        return found
    reports = [p for p in sorted(crash_dir.iterdir()) if p.is_file() and p.stat().st_size > 0]
    if not reports:
        found.append(
            f"{crash_dir} holds no non-empty report: the crash was classified but the "
            "platform's own evidence for it was never captured"
        )
    return found


def _target_scenario(
    scenarios: Sequence[Mapping[str, object]], name: str
) -> Mapping[str, object] | None:
    # `manifest_dict` writes `asdict(RunResult)`, whose scenario-identity field is `scenario`
    # (`bajutsu/common/orchestrator/types/run_result.py`) — the same key `assert_pool_isolation.py`
    # already reads this off.
    return next((s for s in scenarios if s.get("scenario") == name), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="the finished run's directory (runs/<run_id>)")
    parser.add_argument(
        "--scenario",
        default="a crash in the app under test is diagnosed and its report captured",
        help="the scenario name to check (app_crash.yaml's own)",
    )
    args = parser.parse_args()

    manifest_path = args.run_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    scenarios = [s for s in manifest.get("scenarios") or [] if isinstance(s, Mapping)]
    scenario = _target_scenario(scenarios, args.scenario)
    if scenario is None:
        print(f"no scenario named {args.scenario!r} in {manifest_path}", file=sys.stderr)
        return 1

    found = violations(scenario, args.run_dir)
    if found:
        print("app-crash evidence check FAILED:", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("app-crash evidence check passed: the crash was diagnosed and its report captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
