"""Tests for scripts/assert_pool_isolation.py — the concurrent-device pool assertion (BE-0298).

The pool's isolation claim is otherwise proven only of its own bookkeeping (`tests/runner/test_pool.py`
drives `FakeDriver` instances against fabricated udids), so the E2E lane that boots two real devices
asserts it from what the run actually left on disk. These tests pin the decision that lane gates on:
each way a run can contradict the claim — one worker doing all the work, two workers never
overlapping, evidence written under another worker's slug, two results sharing one slug, a directory
no result claims, a recorded evidence dir that never landed — must be reported, and a genuinely
isolated run must report nothing.

`isolation_violations` is pure (parsed manifest scenarios + the run directory's subdirectory names),
so every case here is a synthesized manifest rather than a recorded run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.assert_pool_isolation import (
    artifact_names,
    isolation_violations,
    scenario_window,
)
from scripts.assert_pool_isolation import main as _main


def _scenario(
    name: str,
    sid: str,
    device: str,
    *,
    started_at: float,
    duration: float = 10.0,
    artifacts: list[str] | None = None,
) -> dict[str, object]:
    """One manifest scenario entry, shaped like `asdict(RunResult)`.

    One step carries the absolute `started_at` / `duration_s` pair the overlap check reads, plus the
    step artifact every real run records under `<sid>/<step>/`.
    """
    return {
        "scenario": name,
        "ok": True,
        "sid": sid,
        "device": device,
        "duration_s": duration,
        "artifacts": [{"name": f"{sid}/screen.mp4", "kind": "video", "provider": "simctl"}],
        "steps": [
            {
                "index": 0,
                "action": "tap",
                "ok": True,
                "started_at": started_at,
                "duration_s": duration,
                "artifacts": [
                    {"name": n, "kind": "screenshot", "provider": "xcuitest"}
                    for n in (artifacts if artifacts is not None else [f"{sid}/tap/after.png"])
                ],
            }
        ],
    }


# Two devices, two scenarios each, the two workers' windows genuinely overlapping.
def _isolated_run() -> list[dict[str, object]]:
    return [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0),
        _scenario("search", "01-search", "UDID-B", started_at=1_002.0),
        _scenario("notices", "02-notices", "UDID-A", started_at=1_012.0),
        _scenario("navigation", "03-navigation", "UDID-B", started_at=1_014.0),
    ]


def _dirs(scenarios: list[dict[str, object]]) -> list[str]:
    return sorted(str(s["sid"]) for s in scenarios)


def test_a_genuinely_isolated_two_device_run_reports_nothing() -> None:
    scenarios = _isolated_run()
    assert isolation_violations(scenarios, _dirs(scenarios), expect_devices=2) == []


def test_an_empty_manifest_is_a_violation_rather_than_a_vacuous_pass() -> None:
    assert isolation_violations([], [], expect_devices=2) == [
        "the manifest recorded no scenario results"
    ]


def test_one_worker_doing_every_scenario_fails_the_device_count() -> None:
    scenarios = _isolated_run()
    for s in scenarios:
        s["device"] = "UDID-A"
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("expected at least 2 distinct leased device(s), saw 1" in v for v in violations)


def test_a_device_replaced_mid_run_is_not_read_as_a_pool_violation() -> None:
    # A run that lost a Simulator mid-flight and continued on the replacement the pool adopted
    # (BE-0344) names three devices for a pool of two. Isolation held — more devices is more
    # separation — so the device count must not report it as the pool failing to share the work out.
    scenarios = [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0),
        _scenario("search", "01-search", "UDID-B", started_at=1_002.0),
        _scenario("notices", "02-notices", "UDID-C", started_at=1_012.0),
        _scenario("navigation", "03-navigation", "UDID-B", started_at=1_014.0),
    ]
    assert isolation_violations(scenarios, _dirs(scenarios), expect_devices=2) == []


def test_a_blank_device_is_reported_rather_than_silently_skipped() -> None:
    scenarios = _isolated_run()
    scenarios[1]["device"] = ""
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("recorded no leased device" in v for v in violations)


def test_a_result_with_no_evidence_dir_is_reported() -> None:
    scenarios = _isolated_run()
    scenarios[0]["sid"] = ""
    violations = isolation_violations(scenarios, _dirs(scenarios[1:]), expect_devices=2)
    assert any("recorded no evidence-dir slug" in v for v in violations)


def test_serial_devices_with_no_overlap_fail_the_concurrency_check() -> None:
    # Alternating devices without ever running two at once: the device count passes, the point of a
    # concurrent-device lane does not.
    scenarios = [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0),
        _scenario("search", "01-search", "UDID-B", started_at=1_100.0),
        _scenario("notices", "02-notices", "UDID-A", started_at=1_200.0),
    ]
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert violations == [
        "no two scenarios on different devices overlapped in wall-clock — the run used several "
        "devices but never kept two workers busy at once, so it exercised no real contention"
    ]


def test_two_scenarios_overlapping_on_the_same_device_do_not_count_as_concurrency() -> None:
    # A same-device overlap would mean the pool double-leased one device, which is the opposite of
    # the evidence this check is looking for — it must not satisfy it.
    scenarios = [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0),
        _scenario("search", "01-search", "UDID-A", started_at=1_002.0),
        _scenario("notices", "02-notices", "UDID-B", started_at=1_100.0),
    ]
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("never kept two workers busy at once" in v for v in violations)


def test_a_single_device_run_is_not_asked_for_overlap() -> None:
    scenarios = [_scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0)]
    assert isolation_violations(scenarios, _dirs(scenarios), expect_devices=1) == []


def test_an_artifact_under_another_workers_slug_is_reported() -> None:
    scenarios = _isolated_run()
    scenarios[0]["steps"][0]["artifacts"] = [  # type: ignore[index]
        {"name": "01-search/tap/after.png", "kind": "screenshot", "provider": "xcuitest"}
    ]
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("outside its own evidence dir" in v for v in violations)


@pytest.mark.parametrize("phase", ["before_outcomes", "after_outcomes"])
def test_a_lifecycle_phase_artifact_under_another_workers_slug_is_reported(phase: str) -> None:
    # `setup` and `cleanup` steps write evidence through the same step path, so a cross-write there
    # is the same isolation violation as one in a numbered step.
    scenarios = _isolated_run()
    scenarios[0][phase] = [
        {
            "index": 0,
            "action": "tap",
            "artifacts": [{"name": "01-search/setup/after.png", "kind": "screenshot"}],
        }
    ]
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("'01-search/setup/after.png' outside its own evidence dir" in v for v in violations)


def test_two_results_sharing_one_evidence_dir_is_reported() -> None:
    scenarios = _isolated_run()
    scenarios[1]["sid"] = "00-smoke"
    scenarios[1]["artifacts"] = [
        {"name": "00-smoke/screen.mp4", "kind": "video", "provider": "simctl"}
    ]
    scenarios[1]["steps"][0]["artifacts"] = [  # type: ignore[index]
        {"name": "00-smoke/tap/after.png", "kind": "screenshot", "provider": "xcuitest"}
    ]
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("is claimed by 2 scenarios" in v for v in violations)


def test_a_directory_no_result_claims_is_reported() -> None:
    scenarios = _isolated_run()
    dirs = [*_dirs(scenarios), "04-orphan"]
    violations = isolation_violations(scenarios, dirs, expect_devices=2)
    assert any("'04-orphan/'" in v for v in violations)


def test_a_result_whose_evidence_dir_never_landed_is_reported() -> None:
    # The mirror of the orphan case: the result names artifacts under `01-search/`, but the run
    # directory has no such dir — a worker whose evidence was dropped on a contended host.
    scenarios = _isolated_run()
    dirs = [d for d in _dirs(scenarios) if d != "01-search"]
    violations = isolation_violations(scenarios, dirs, expect_devices=2)
    assert any(
        "recorded evidence under '01-search', which the run directory does not hold" in v
        for v in violations
    )


def test_a_result_that_recorded_no_evidence_is_not_asked_for_a_directory() -> None:
    # Nothing was captured, so no directory is expected: the check must claim only that recorded
    # evidence landed, not that every result captured something.
    scenarios = _isolated_run()
    scenarios[1]["artifacts"] = []
    scenarios[1]["steps"][0]["artifacts"] = []  # type: ignore[index]
    dirs = [d for d in _dirs(scenarios) if d != "01-search"]
    assert isolation_violations(scenarios, dirs, expect_devices=2) == []


def test_artifact_names_collects_the_scenario_and_its_steps() -> None:
    scenario = _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0)
    assert artifact_names(scenario) == ["00-smoke/screen.mp4", "00-smoke/tap/after.png"]


def test_scenario_window_ignores_a_step_with_no_absolute_instant() -> None:
    # A pre-v6 manifest (or a step that never started) carries started_at = 0; counting it would put
    # the window's start at the epoch and make it overlap everything.
    scenario = _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0)
    scenario["steps"].append(  # type: ignore[attr-defined]
        {"index": 1, "action": "tap", "ok": True, "started_at": 0.0, "duration_s": 3.0}
    )
    assert scenario_window(scenario) == (1_000.0, 1_010.0)


def test_scenario_window_is_none_when_no_step_recorded_an_instant() -> None:
    assert scenario_window({"steps": [{"index": 0, "action": "tap", "started_at": 0.0}]}) is None
    assert scenario_window({"steps": []}) is None
    # An anchor alone never makes a window: on a pre-v6 run it would be relative seconds, not epoch.
    assert scenario_window({"steps": [], "video_anchor_s": 1_000.0}) is None


def test_scenario_window_starts_at_the_recording_anchor_before_the_first_step() -> None:
    # The lease's app launch and readiness wait run after the recording starts and before the first
    # step, so the window must open at the anchor rather than at the step (issue #2419).
    scenario = _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0, duration=1.0)
    scenario["video_anchor_s"] = 985.0
    assert scenario_window(scenario) == (985.0, 1_001.0)


def test_scenario_window_ignores_a_missing_or_later_anchor() -> None:
    scenario = _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0)
    # Zero is what a run with no recording (or one predating manifest v6) carries.
    scenario["video_anchor_s"] = 0.0
    assert scenario_window(scenario) == (1_000.0, 1_010.0)
    # An anchor after the first step never narrows the window the steps already prove.
    scenario["video_anchor_s"] = 1_004.0
    assert scenario_window(scenario) == (1_000.0, 1_010.0)


def test_scenario_window_spans_the_setup_and_cleanup_phases() -> None:
    scenario = _scenario("smoke", "00-smoke", "UDID-A", started_at=1_000.0)
    scenario["before_outcomes"] = [
        {"index": 0, "action": "tap", "started_at": 990.0, "duration_s": 2.0}
    ]
    scenario["after_outcomes"] = [
        {"index": 0, "action": "tap", "started_at": 1_020.0, "duration_s": 3.0}
    ]
    assert scenario_window(scenario) == (990.0, 1_023.0)


def test_concurrent_bring_up_counts_as_overlap_when_the_steps_alone_miss() -> None:
    # The shape issue #2419 recorded on an Android pool run: each device spends ~12 s launching the
    # app before a short scenario's steps, so no two step windows on different devices meet, while
    # each worker's bring-up ran during the other worker's steps.
    scenarios = [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_013.0, duration=1.0),
        _scenario("firstlook", "01-firstlook", "UDID-B", started_at=1_018.0, duration=12.0),
        _scenario("navigation", "02-navigation", "UDID-A", started_at=1_032.0, duration=20.0),
        _scenario("interrupts", "03-interrupts", "UDID-B", started_at=1_055.0, duration=7.0),
    ]
    for scenario, anchor in zip(scenarios, (1_000.0, 1_004.0, 1_020.0, 1_042.0), strict=True):
        scenario["video_anchor_s"] = anchor
    assert isolation_violations(scenarios, _dirs(scenarios), expect_devices=2) == []


def test_serial_scenarios_still_fail_once_the_bring_up_is_counted() -> None:
    # A pool that ran one scenario at a time, bring-up included, leaves disjoint windows even with the
    # anchor counted, so the concurrency check still fails it.
    scenarios = [
        _scenario("smoke", "00-smoke", "UDID-A", started_at=1_013.0, duration=1.0),
        _scenario("firstlook", "01-firstlook", "UDID-B", started_at=1_030.0, duration=12.0),
        _scenario("navigation", "02-navigation", "UDID-A", started_at=1_058.0, duration=20.0),
    ]
    for scenario, anchor in zip(scenarios, (1_000.0, 1_015.0, 1_045.0), strict=True):
        scenario["video_anchor_s"] = anchor
    violations = isolation_violations(scenarios, _dirs(scenarios), expect_devices=2)
    assert any("never kept two workers busy at once" in v for v in violations)


def test_main_passes_on_a_real_isolated_run_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = _isolated_run()
    for sid in _dirs(scenarios):
        (tmp_path / sid).mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schemaVersion": 8, "scenarios": scenarios})
    )
    assert _main_with(tmp_path, 2) == 0
    assert "pool isolation holds: 4 scenarios across 2 devices" in capsys.readouterr().out


def test_main_fails_loudly_on_an_unreadable_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main_with(tmp_path, 2) == 1
    assert "cannot read the run manifest" in capsys.readouterr().out


def test_main_fails_when_the_manifest_holds_no_scenarios(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"schemaVersion": 8}))
    assert _main_with(tmp_path, 2) == 1
    assert "holds no scenario results" in capsys.readouterr().out


def test_main_exempts_the_video_staging_directory(tmp_path: Path) -> None:
    # `bajutsu/common/runner/pool.py` reserves `_video_tmp` at the run dir's top level on a platform that
    # starts recording before the app launches (Android, web), and leaves the directory behind once
    # the recording has moved into the scenario's own dir. It belongs to no worker, so reading it as
    # an orphan would fail an isolation check the run actually passed.
    scenarios = _isolated_run()
    for sid in _dirs(scenarios):
        (tmp_path / sid).mkdir()
    (tmp_path / "_video_tmp").mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schemaVersion": 8, "scenarios": scenarios})
    )
    assert _main_with(tmp_path, 2) == 0


def test_main_skips_a_symlinked_directory(tmp_path: Path) -> None:
    # A symlink is not a scenario's evidence dir; following it would report a spurious orphan.
    scenarios = _isolated_run()
    for sid in _dirs(scenarios):
        (tmp_path / sid).mkdir()
    (tmp_path / "elsewhere").symlink_to(tmp_path / "00-smoke")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"schemaVersion": 8, "scenarios": scenarios})
    )
    assert _main_with(tmp_path, 2) == 0


def _main_with(run_dir: Path, expect_devices: int) -> int:
    """Invoke the script's `main` with a synthesized argv."""
    import sys

    argv = sys.argv
    sys.argv = [
        "assert_pool_isolation.py",
        "--run-dir",
        str(run_dir),
        "--expect-devices",
        str(expect_devices),
    ]
    try:
        return _main()
    finally:
        sys.argv = argv
