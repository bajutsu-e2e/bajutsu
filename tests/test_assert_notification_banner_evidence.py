"""Tests for scripts/assert_notification_banner_evidence.py — the heads-up proof (BE-0496 Unit 6).

The Android emulator lane gates on this script, because the scenario's own tap would pass on a run
in which the notification never appeared. Every case is a synthesized manifest scenario, the shape
`tests/test_assert_app_crash_evidence.py` uses for its own on-device claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bajutsu.common.drivers.adb import BANNER_GUARD_OFF_NOTE
from scripts.assert_notification_banner_evidence import main as _main
from scripts.assert_notification_banner_evidence import relaunches, violations

_NAME = "a heads-up notification over a control is cleared before the tap"
_DISMISSAL = {"label": "Showcase notification", "kind": "notificationBanner"}


def _scenario(
    *,
    ok: bool = True,
    alerts: list[dict[str, str]] | None = None,
    expect_alerts: list[dict[str, str]] | None = None,
    warnings: list[str] | None = None,
    channel_notes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "scenario": _NAME,
        "ok": ok,
        "failure": None if ok else "assert failed",
        "steps": [
            {"index": 0, "action": "tap", "ok": True, "alerts": [], "warnings": []},
            {
                "index": 1,
                "action": "tap",
                "ok": ok,
                "alerts": alerts or [],
                "warnings": warnings or [],
            },
        ],
        "expect_alerts": expect_alerts or [],
        "channel_notes": channel_notes or [],
        "channel_degraded": "",
    }


def test_a_passing_run_with_a_step_dismissal_is_sound() -> None:
    assert violations(_scenario(alerts=[_DISMISSAL])) == []


def test_a_dismissal_in_the_expect_phase_counts_too() -> None:
    assert violations(_scenario(expect_alerts=[_DISMISSAL])) == []


def test_a_run_with_no_dismissal_exercised_nothing() -> None:
    found = violations(_scenario())
    assert len(found) == 1
    assert "no `notificationBanner` dismissal" in found[0]


def test_an_alert_dismissal_is_not_a_banner_dismissal() -> None:
    found = violations(_scenario(alerts=[{"label": "Allow", "kind": "alert"}]))
    assert any("no `notificationBanner` dismissal" in line for line in found)


def test_a_failed_tap_is_named() -> None:
    found = violations(_scenario(ok=False, alerts=[_DISMISSAL]))
    assert len(found) == 1
    assert "did not take its tap" in found[0]


@pytest.mark.parametrize("where", ["warnings", "channel_notes"])
def test_a_run_whose_guard_was_off_fails_with_that_cause_alone(where: str) -> None:
    scenario = _scenario(**{where: [BANNER_GUARD_OFF_NOTE]})  # type: ignore[arg-type]
    found = violations(scenario)
    assert found == [
        "the resident channel was unavailable, so the banner guard was off: reads went "
        "through uiautomator dump, which cannot see a heads-up notification"
    ]


def _main_with(run_dir: Path) -> int:
    argv = sys.argv
    sys.argv = ["assert_notification_banner_evidence.py", str(run_dir)]
    try:
        return _main()
    finally:
        sys.argv = argv


def test_main_reads_the_manifest_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"scenarios": [_scenario(alerts=[_DISMISSAL])]})
    )
    assert _main_with(tmp_path) == 0
    assert "check passed" in capsys.readouterr().out


def test_main_fails_on_missing_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": [_scenario()]}))
    assert _main_with(tmp_path) == 1
    assert "check FAILED" in capsys.readouterr().err


def test_main_fails_without_a_manifest_or_the_scenario(tmp_path: Path) -> None:
    assert _main_with(tmp_path) == 1
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": [{"scenario": "other"}]}))
    assert _main_with(tmp_path) == 1


# The events-buffer lines the failing CI run left (issue #2520), plus the launcher's own relaunch at
# boot, which belongs to another package and must not count.
_VIEWS = "com.bajutsu.showcase.android.views"
_EVENTS = (
    "11-25 01:57:14.453   522   539 I wm_relaunch_resume_activity: "
    "[0,147066410,7,com.google.android.apps.nexuslauncher/.NexusLauncherActivity,80000000]\n"
    "11-25 01:58:56.354   522   522 I notification_canceled: "
    f"[0|{_VIEWS}|4960|null|10195,5,3592,3592,2724,-1,-1,NULL]\n"
    "11-25 01:58:56.355   522   539 I wm_relaunch_resume_activity: "
    f"[0,222635148,9,{_VIEWS}/com.bajutsu.showcase.views.MainActivity,80000000]\n"
)


def test_relaunches_picks_the_apps_own_activity_alone() -> None:
    assert relaunches(_EVENTS, _VIEWS) == [
        f"{_VIEWS}/com.bajutsu.showcase.views.MainActivity (config-change mask 80000000)"
    ]
    assert relaunches(_EVENTS, "com.bajutsu.showcase.android.compose") == []


def test_relaunches_reads_the_non_resumed_variant_too() -> None:
    line = f"I wm_relaunch_activity: [0,1,2,{_VIEWS}/.Main,80000000]"
    assert relaunches(line, _VIEWS) == [f"{_VIEWS}/.Main (config-change mask 80000000)"]


def test_a_system_relaunch_is_named_with_its_mask_on_a_failure() -> None:
    found = violations(_scenario(ok=False), relaunches(_EVENTS, _VIEWS))
    assert "(config-change mask 80000000) mid-run" in found[0]
    assert _VIEWS in found[0]
    assert any("did not take its tap" in line for line in found[1:])


def test_a_relaunch_never_fails_a_sound_run() -> None:
    assert violations(_scenario(alerts=[_DISMISSAL]), relaunches(_EVENTS, _VIEWS)) == []


def _main_with_events(run_dir: Path, events: Path, package: str) -> int:
    argv = sys.argv
    sys.argv = [
        "assert_notification_banner_evidence.py",
        "--events",
        str(events),
        "--package",
        package,
        str(run_dir),
    ]
    try:
        return _main()
    finally:
        sys.argv = argv


def test_main_names_a_relaunch_from_the_event_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": [_scenario(ok=False)]}))
    events = tmp_path / "device-events.log"
    events.write_text(_EVENTS)
    assert _main_with_events(tmp_path, events, _VIEWS) == 1
    assert "config-change mask 80000000" in capsys.readouterr().err


def test_main_fails_on_a_missing_event_log(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps({"scenarios": [_scenario(alerts=[_DISMISSAL])]})
    )
    assert _main_with_events(tmp_path, tmp_path / "absent.log", _VIEWS) == 1


def test_main_requires_events_and_package_together(tmp_path: Path) -> None:
    argv = sys.argv
    sys.argv = ["assert_notification_banner_evidence.py", "--events", "x.log", str(tmp_path)]
    try:
        with pytest.raises(SystemExit):
            _main()
    finally:
        sys.argv = argv


def test_relaunches_skips_a_malformed_tuple() -> None:
    assert relaunches(f"I wm_relaunch_activity: [0,1,2,{_VIEWS}/.Main]", _VIEWS) == []


def test_a_repeated_relaunch_is_named_once() -> None:
    twice = relaunches(_EVENTS + _EVENTS, _VIEWS)
    assert len(twice) == 2
    found = violations(_scenario(ok=False), twice)
    assert found[0].count(_VIEWS) == 1


@pytest.mark.parametrize("text", ["", "--------- beginning of events\n"])
@pytest.mark.parametrize(("alerts", "code"), [([_DISMISSAL], 0), ([], 1)])
def test_main_warns_on_an_event_log_with_no_events(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    text: str,
    alerts: list[dict[str, str]],
    code: int,
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": [_scenario(alerts=alerts)]}))
    events = tmp_path / "device-events.log"
    events.write_text(text)
    assert _main_with_events(tmp_path, events, _VIEWS) == code
    assert "holds no events" in capsys.readouterr().err


def test_a_different_mask_is_reported_as_itself() -> None:
    line = f"I wm_relaunch_resume_activity: [0,1,2,{_VIEWS}/.Main,80]"
    found = violations(_scenario(ok=False), relaunches(line, _VIEWS))
    assert "(config-change mask 80) mid-run" in found[0]
