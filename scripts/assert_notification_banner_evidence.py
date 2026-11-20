#!/usr/bin/env python3
"""Assert a real Android heads-up notification was cleared and reported (BE-0496).

`notification_banner_android.yaml` posts a heads-up notification and then taps a control the
banner covers. The tap alone proves nothing: a run in which the notification never showed would
pass on it and exercise nothing at all. No scenario assertion can read the run's `AlertEvent`s
either — a scenario cannot observe a banner — so the proof runs after the scenario, from what the
finished run left in its manifest, the same shape `assert_app_crash_evidence.py` uses for its own
on-device claim.

The fast suite proves the frame extraction against dumps recorded on API 33-35 emulators, and the
clearing against a scripted device. What no unit test can show is that a real swipe dismisses a real
heads-up notification, so this is the check the Android emulator lane gates on:

- **The scenario passed.** The control under the banner took its tap, rather than the banner taking
  it and opening the notification's app.
- **The scenario carries a `notificationBanner` dismissal.** Both clearing paths record one — the
  driver's check before a gesture, and the orchestrator's per-step sweep — so a banner that appeared
  leaves the record whichever path cleared it.
- **The resident channel stayed up.** The `uiautomator dump` fallback cannot see SystemUI's
  heads-up window, so the guard is off there; such a run fails with that cause named, never as a
  missing dismissal, and a run that could not verify the guard never passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from bajutsu.common.drivers.adb import BANNER_GUARD_OFF_NOTE

_PHASES = ("before_outcomes", "steps", "after_outcomes")


def _outcomes(scenario: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Every step outcome the scenario recorded, across its three lifecycle phases."""
    found: list[Mapping[str, object]] = []
    for phase in _PHASES:
        entries = scenario.get(phase)
        if isinstance(entries, list):
            found.extend(o for o in entries if isinstance(o, Mapping))
    return found


def _alerts(scenario: Mapping[str, object]) -> list[Mapping[str, object]]:
    found: list[Mapping[str, object]] = []
    for holder in [*_outcomes(scenario), {"alerts": scenario.get("expect_alerts")}]:
        entries = holder.get("alerts")
        if isinstance(entries, list):
            found.extend(a for a in entries if isinstance(a, Mapping))
    return found


def _notes(scenario: Mapping[str, object]) -> list[str]:
    """The scenario's channel notes and every step's warnings, where a channel note lands."""
    found: list[object] = []
    for holder in [scenario, *_outcomes(scenario)]:
        for key in ("channel_notes", "warnings"):
            entries = holder.get(key)
            if isinstance(entries, list):
                found.extend(entries)
    degraded = scenario.get("channel_degraded")
    if isinstance(degraded, str) and degraded:
        found.append(degraded)
    return [n for n in found if isinstance(n, str)]


def violations(scenario: Mapping[str, object]) -> list[str]:
    """Everything wrong with this scenario's banner evidence, empty when it is sound."""
    if any(BANNER_GUARD_OFF_NOTE in note for note in _notes(scenario)):
        return [
            "the resident channel was unavailable, so the banner guard was off: reads went "
            "through uiautomator dump, which cannot see a heads-up notification"
        ]
    found: list[str] = []
    if scenario.get("ok") is not True:
        found.append(
            f"the scenario failed ({scenario.get('failure') or 'no reason recorded'}): the control "
            "under the banner did not take its tap"
        )
    if not any(a.get("kind") == "notificationBanner" for a in _alerts(scenario)):
        found.append(
            "no `notificationBanner` dismissal was recorded: either the heads-up notification "
            "never appeared, so the run exercised nothing, or it was cleared without a record"
        )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="the finished run's directory (runs/<run_id>)")
    parser.add_argument(
        "--scenario",
        default="a heads-up notification over a control is cleared before the tap",
        help="the scenario name to check (notification_banner_android.yaml's own)",
    )
    args = parser.parse_args()

    manifest_path = args.run_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text())
    scenario = next(
        (
            s
            for s in manifest.get("scenarios") or []
            if isinstance(s, Mapping) and s.get("scenario") == args.scenario
        ),
        None,
    )
    if scenario is None:
        print(f"no scenario named {args.scenario!r} in {manifest_path}", file=sys.stderr)
        return 1
    found = violations(scenario)
    if found:
        print("notification-banner evidence check FAILED:", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("notification-banner evidence check passed: the banner was cleared and reported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
