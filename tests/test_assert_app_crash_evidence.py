"""Tests for scripts/assert_app_crash_evidence.py — the app-crash-diagnosis assertion (BE-0424).

Both `app-crash (xcuitest)` and `app-crash (adb)` discard the wrapped `bajutsu run`'s own exit code
on purpose (`app_crash.yaml` is expected to fail), so `violations()` alone decides those jobs. These
tests pin the three claims it makes — the scenario failed, exactly one step outcome carries
`app_crashed`, and the `app-crash/` directory holds a non-empty report — against a synthesized
manifest dict, the same shape `tests/test_assert_pool_isolation.py` uses for its own on-device claim.

`violations` is pure (a parsed manifest scenario + the run directory), so every case here is a
synthesized dict rather than a recorded run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.assert_app_crash_evidence import main as _main
from scripts.assert_app_crash_evidence import violations

_SCENARIO_NAME = "a crash in the app under test is diagnosed and its report captured"


def _outcome(*, app_crashed: bool | None = None) -> dict[str, object]:
    outcome: dict[str, object] = {"index": 0, "action": "tap", "ok": app_crashed is not True}
    if app_crashed is not None:
        outcome["app_crashed"] = app_crashed
    return outcome


def _scenario(
    *,
    ok: bool = False,
    sid: str | None = "00-app_crash",
    steps: list[dict[str, object]] | None = None,
    before_outcomes: list[dict[str, object]] | None = None,
    after_outcomes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    scenario: dict[str, object] = {
        "scenario": _SCENARIO_NAME,
        "ok": ok,
        "steps": steps if steps is not None else [_outcome(), _outcome(app_crashed=True)],
    }
    if sid is not None:
        scenario["sid"] = sid
    if before_outcomes is not None:
        scenario["before_outcomes"] = before_outcomes
    if after_outcomes is not None:
        scenario["after_outcomes"] = after_outcomes
    return scenario


def _crash_dir(tmp_path: Path, sid: str = "00-app_crash") -> Path:
    d = tmp_path / sid / "app-crash"
    d.mkdir(parents=True)
    return d


def test_a_sound_run_reports_nothing(tmp_path: Path) -> None:
    crash_dir = _crash_dir(tmp_path)
    (crash_dir / "crash.ips").write_text("...")
    assert violations(_scenario(), tmp_path) == []


def test_a_scenario_that_passed_is_reported() -> None:
    # A green run means the fixture never faulted at all — the affordance compiled out, or the
    # trigger id moved — which would otherwise pass as "no crash detected" and prove nothing.
    found = violations(_scenario(ok=True), Path("/nonexistent"))
    assert any("the scenario passed" in v for v in found)


def test_a_scenario_reporting_ok_none_is_not_read_as_passed() -> None:
    # `scenario.get("ok") is not False` — an absent or non-boolean `ok` must still be caught, not
    # waved through by a truthiness check that would treat `None` as "not explicitly failed".
    scenario = _scenario()
    del scenario["ok"]
    found = violations(scenario, Path("/nonexistent"))
    assert any("the scenario passed" in v for v in found)


def test_no_outcome_carrying_app_crashed_is_reported() -> None:
    found = violations(_scenario(steps=[_outcome(), _outcome()]), Path("/nonexistent"))
    assert any("no step outcome carries `app_crashed`" in v for v in found)


def test_two_outcomes_carrying_app_crashed_is_reported() -> None:
    scenario = _scenario(steps=[_outcome(app_crashed=True), _outcome(app_crashed=True)])
    found = violations(scenario, Path("/nonexistent"))
    assert any("2 step outcomes carry `app_crashed`" in v for v in found)


def test_the_crashed_outcome_is_found_in_before_or_after_outcomes(tmp_path: Path) -> None:
    # The crashed outcome is not always in `steps`: a `before` step's failure leaves `steps` empty
    # outright, and an `after` rule dispatched on the failure records into its own list.
    crash_dir = _crash_dir(tmp_path)
    (crash_dir / "crash.ips").write_text("...")
    scenario = _scenario(steps=[], before_outcomes=[_outcome(app_crashed=True)])
    assert violations(scenario, tmp_path) == []

    scenario = _scenario(steps=[_outcome()], after_outcomes=[_outcome(app_crashed=True)])
    assert violations(scenario, tmp_path) == []


def test_a_missing_sid_is_reported_and_short_circuits_the_directory_check() -> None:
    found = violations(_scenario(sid=None), Path("/nonexistent"))
    assert any("records no `sid`" in v for v in found)
    # No sid means no evidence directory can be located — nothing else to check.
    assert len(found) == 1


def test_a_blank_sid_is_reported_the_same_as_a_missing_one() -> None:
    found = violations(_scenario(sid=""), Path("/nonexistent"))
    assert any("records no `sid`" in v for v in found)


def test_a_missing_evidence_directory_is_reported(tmp_path: Path) -> None:
    found = violations(_scenario(), tmp_path)
    assert any("no app-crash/ directory" in v for v in found)


def test_an_empty_evidence_directory_is_reported(tmp_path: Path) -> None:
    _crash_dir(tmp_path)
    found = violations(_scenario(), tmp_path)
    assert any("holds no non-empty report" in v for v in found)


def test_a_zero_byte_report_does_not_satisfy_the_check(tmp_path: Path) -> None:
    # A classification with an empty file behind it is the same failure as no file at all: the
    # point of the item is the evidence, not the label.
    crash_dir = _crash_dir(tmp_path)
    (crash_dir / "crash.ips").write_text("")
    found = violations(_scenario(), tmp_path)
    assert any("holds no non-empty report" in v for v in found)


def test_multiple_violations_are_all_reported_together(tmp_path: Path) -> None:
    found = violations(_scenario(ok=True, steps=[_outcome(), _outcome()]), tmp_path)
    assert any("the scenario passed" in v for v in found)
    assert any("no step outcome carries `app_crashed`" in v for v in found)
    assert any("no app-crash/ directory" in v for v in found)
    assert len(found) == 3


def _write_manifest(tmp_path: Path, scenario: dict[str, object]) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"scenarios": [scenario]}))


def test_main_passes_on_a_real_sound_run_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    crash_dir = _crash_dir(tmp_path)
    (crash_dir / "crash.ips").write_text("...")
    _write_manifest(tmp_path, _scenario())
    assert _main_with(tmp_path) == 0
    assert "the crash was diagnosed and its report captured" in capsys.readouterr().out


def test_main_fails_loudly_on_a_missing_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main_with(tmp_path) == 1
    assert "no manifest at" in capsys.readouterr().err


def test_main_fails_when_the_named_scenario_is_absent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _scenario()
    scenario["scenario"] = "some other scenario"
    _write_manifest(tmp_path, scenario)
    assert _main_with(tmp_path) == 1
    assert "no scenario named" in capsys.readouterr().err


def test_main_reports_each_violation_on_a_failing_scenario(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_manifest(tmp_path, _scenario(ok=True, steps=[_outcome()]))
    assert _main_with(tmp_path) == 1
    err = capsys.readouterr().err
    assert "app-crash evidence check FAILED" in err
    assert "the scenario passed" in err


def _main_with(run_dir: Path) -> int:
    """Invoke the script's `main` with a synthesized argv."""
    import sys

    argv = sys.argv
    sys.argv = ["assert_app_crash_evidence.py", str(run_dir)]
    try:
        return _main()
    finally:
        sys.argv = argv
