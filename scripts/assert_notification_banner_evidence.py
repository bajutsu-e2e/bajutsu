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

Given the device's event log for the run (`--events`) and the app's package, a failure also names
every relaunch of the app's activity, with its config-change mask (issue #2520). On CI one such
relaunch put the app back on its first tab mid-run, and the scenario failed on a selector miss that
said nothing of it. The report states the relaunch and not its cause, since passing runs have
carried one too. The one seen came with the late handling of the app's own install, which `adb.Env`'s
`install` waits out.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from bajutsu.common.drivers.adb import BANNER_GUARD_OFF_NOTE

_PHASES = ("before_outcomes", "steps", "after_outcomes")
# `wm_relaunch_resume_activity: [user,token,task,<package>/<activity>,<config changes>]`, and its
# `wm_relaunch_activity` twin for an activity that was not resumed.
_RELAUNCH = re.compile(r"\bwm_relaunch(?:_resume)?_activity: \[([^\]]*)\]")


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


def relaunches(events: str, package: str) -> list[str]:
    """Each relaunch of `package`'s activities with its config-change mask, in log order."""
    found: list[str] = []
    for match in _RELAUNCH.finditer(events):
        fields = match.group(1).split(",")
        if len(fields) >= 5 and fields[3].split("/", 1)[0] == package:
            found.append(f"{fields[3]} (config-change mask {fields[4]})")
    return found


def violations(scenario: Mapping[str, object], relaunched: Sequence[str] = ()) -> list[str]:
    """Everything wrong with this scenario's banner evidence, empty when it is sound.

    Args:
        scenario: The scenario's entry in the run manifest.
        relaunched: The app's activity relaunches during the run (`relaunches`). They add context
            to a failure; they never fail a run that is otherwise sound.
    """
    if any(BANNER_GUARD_OFF_NOTE in note for note in _notes(scenario)):
        return [
            "the resident channel was unavailable, so the banner guard was off: reads went "
            "through uiautomator dump, which cannot see a heads-up notification"
        ]
    found: list[str] = []
    if relaunched and (
        scenario.get("ok") is not True
        or not any(a.get("kind") == "notificationBanner" for a in _alerts(scenario))
    ):
        found.append(
            f"the system relaunched {', '.join(dict.fromkeys(relaunched))} mid-run, which can "
            "reset the app's screen. Mask 80000000 (assets paths) is the one seen in issue #2520, "
            "where package changes were in flight; any other mask is a different event. Read "
            "`device-events.log` before ruling the driver out"
        )
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
    parser.add_argument(
        "--events",
        type=Path,
        help="the device's `logcat -b events` output for the run, to name a system relaunch",
    )
    parser.add_argument("--package", help="the app's package, required with --events")
    args = parser.parse_args()
    if (args.events is None) != (args.package is None):
        parser.error("--events and --package go together")

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
    relaunched: list[str] = []
    if args.events is not None:
        if not args.events.is_file():
            print(f"no device event log at {args.events}", file=sys.stderr)
            return 1
        events = args.events.read_text(errors="replace")
        # `logcat` heads an empty buffer with a `--------- beginning of` line, which is no event.
        if not any(
            line.strip() and not line.startswith("---------") for line in events.splitlines()
        ):
            print(
                f"warning: the device event log at {args.events} holds no events, so a system "
                "relaunch could not be ruled out",
                file=sys.stderr,
            )
        relaunched = relaunches(events, args.package)
    found = violations(scenario, relaunched)
    if found:
        print("notification-banner evidence check FAILED:", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("notification-banner evidence check passed: the banner was cleared and reported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
