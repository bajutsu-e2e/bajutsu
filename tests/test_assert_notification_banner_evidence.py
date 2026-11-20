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
from scripts.assert_notification_banner_evidence import violations

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
