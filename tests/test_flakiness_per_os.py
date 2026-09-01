"""Both flakiness surfaces group per device OS version (BE-0358).

The measured case this exists for: one scenario, one content fingerprint, two OS versions, opposite
verdicts. Each is perfectly deterministic on the OS it ran on, so the audit must report two
deterministic histories — "this scenario does not work on that OS" — where it used to report one
flaky one. The file-backed audit (`longitudinal`) and the hosted ranking (`rank_flakiness`) share the
classification, so they must gain the same OS component and the same unknown-key rule; the stats
aggregate joins durations against the same widened key.
"""

from __future__ import annotations

from bajutsu.analysis.audit import longitudinal, render_longitudinal
from bajutsu.analysis.stats import aggregate_runs, target_metrics
from bajutsu.serve.flakiness import rank_flakiness, records_from_manifests, render
from bajutsu.serve.server.db import RunRecord

_HASH = "sha256:c6b97d3d1"


def _manifest(
    run_id: str,
    *,
    ok: bool,
    runtime: str = "iOS 18.6",
    name: str = "native alert can be tapped",
    duration_s: float = 1.0,
) -> dict[str, object]:
    """A run manifest as the runner writes it — one scenario, stamped with the device it ran on."""
    return {
        "runId": run_id,
        "ok": ok,
        "provenance": {"scenarioHash": _HASH},
        "scenarios": [
            {"scenario": name, "ok": ok, "device_runtime": runtime, "duration_s": duration_s}
        ],
    }


def _record(run_id: str, *, ok: bool, runtime: str | None) -> RunRecord:
    """A finished run record carrying just what the hosted ranking reads."""
    return RunRecord(
        id=run_id,
        org_id="o1",
        status="done",
        ok=ok,
        summary={"scenarios": ["native alert can be tapped"]},
        scenario_hash=_HASH,
        device_runtime=runtime,
    )


# --- the file-backed audit (`bajutsu audit --history`) ---


def test_longitudinal_splits_one_flaky_history_into_two_deterministic_ones() -> None:
    # It passed every time on iOS 18.6 and failed every time on iOS 26.5. Pooled, that is a 2/4 mix
    # at a constant fingerprint — flakiness. Split by OS, each half is reproducible on its own.
    report = longitudinal(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-013908", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-014335", ok=False, runtime="iOS 26.5"),
            _manifest("20260812-014336", ok=False, runtime="iOS 26.5"),
        ]
    )
    assert [h.classification for h in report.histories] == ["deterministic", "deterministic"]
    # One fingerprint, one name — only the OS tells the two histories apart.
    assert {h.scenario_hash for h in report.histories} == {_HASH}
    assert [h.device_os.display for h in report.histories if h.device_os] == [
        "iOS 18.6",
        "iOS 26.5",
    ]


def test_longitudinal_still_flags_a_mix_on_one_os_as_flaky() -> None:
    """The split must narrow nothing it should not: a flip on a single OS is still true flakiness."""
    report = longitudinal(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-014335", ok=False, runtime="iOS 18.6"),
        ]
    )
    (h,) = report.histories
    assert h.classification == "flaky" and h.runs == 2


def test_longitudinal_treats_a_patch_release_as_the_same_os() -> None:
    runs = [
        _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
        _manifest("20260812-014335", ok=False, runtime="iOS 18.6.1"),
    ]
    (h,) = longitudinal(runs).histories
    assert h.runs == 2 and h.classification == "flaky"
    # The merged group holds two spellings, so the reported label is the canonical one either way —
    # `--json` must not change with the order the manifests were read in.
    assert h.device_os is not None and h.device_os.label == "iOS 18.6"
    (reversed_h,) = longitudinal(list(reversed(runs))).histories
    assert reversed_h.device_os is not None and reversed_h.device_os.label == "iOS 18.6"


def test_longitudinal_keeps_an_unrecorded_os_in_its_own_history() -> None:
    """An absent or unrecognized label joins no version's history, and is not dropped either."""
    report = longitudinal(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-014335", ok=False, runtime=""),
            _manifest("20260812-015000", ok=False, runtime="chromium"),
        ]
    )
    assert len(report.histories) == 2
    unknown = [h for h in report.histories if h.device_os is None]
    assert len(unknown) == 1 and unknown[0].runs == 2  # both unrecognized runs, one history


def test_render_longitudinal_names_the_os_and_discloses_the_unknown_split() -> None:
    out = render_longitudinal(
        longitudinal(
            [
                _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
                _manifest("20260812-014335", ok=False, runtime=""),
            ]
        )
    )
    assert "on iOS 18.6" in out
    assert "on unknown OS" in out
    assert "1 history with no single recorded device OS" in out


def test_longitudinal_orders_two_os_histories_deterministically() -> None:
    """Both sort keys tie for a pair that differs only by OS, so the OS has to break it."""
    forward = longitudinal(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 26.5"),
            _manifest("20260812-014335", ok=True, runtime="iOS 18.6"),
        ]
    )
    reverse = longitudinal(
        [
            _manifest("20260812-014335", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-013907", ok=True, runtime="iOS 26.5"),
        ]
    )
    order = [h.device_os.display for h in forward.histories if h.device_os]
    assert order == ["iOS 18.6", "iOS 26.5"]
    assert order == [h.device_os.display for h in reverse.histories if h.device_os]


# --- the hosted ranking (`serve` panel / `bajutsu flakiness`) ---


def test_rank_flakiness_splits_the_same_case_by_os() -> None:
    report = rank_flakiness(
        [
            _record("20260812-013907", ok=True, runtime="iOS 18.6"),
            _record("20260812-013908", ok=True, runtime="iOS 18.6"),
            _record("20260812-014335", ok=False, runtime="iOS 26.5"),
            _record("20260812-014336", ok=False, runtime="iOS 26.5"),
        ]
    )
    assert [s.classification for s in report.scenarios] == ["deterministic", "deterministic"]
    assert [s.device_os.display for s in report.scenarios if s.device_os] == [
        "iOS 18.6",
        "iOS 26.5",
    ]


def test_rank_flakiness_still_flags_a_mix_on_one_os() -> None:
    report = rank_flakiness(
        [
            _record("20260812-013907", ok=True, runtime="iOS 18.6"),
            _record("20260812-014335", ok=False, runtime="iOS 18.6"),
        ]
    )
    (s,) = report.scenarios
    assert s.classification == "flaky" and s.flip_rate == 1.0


def test_rank_flakiness_groups_an_undetermined_os_under_unknown() -> None:
    """A row recorded before the OS was tracked (None) and one that named none ("") share the key."""
    report = rank_flakiness(
        [
            _record("20260812-013907", ok=True, runtime=None),
            _record("20260812-014335", ok=False, runtime=""),
        ]
    )
    (s,) = report.scenarios
    assert s.device_os is None and s.runs == 2


def test_render_flakiness_names_the_os_and_discloses_the_unknown_split() -> None:
    out = render(
        rank_flakiness(
            [
                _record("20260812-013907", ok=True, runtime="iOS 18.6"),
                _record("20260812-014335", ok=False, runtime=None),
            ]
        )
    )
    assert "on iOS 18.6" in out
    assert "on unknown OS" in out
    assert "1 history with no single recorded device OS" in out


def test_records_from_manifests_fills_the_os_the_db_column_carries() -> None:
    """The file-backed and database-free callers must fill the same field, or they'd group as unknown."""
    records = records_from_manifests(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-013908", ok=True, runtime="iOS 18.6"),
            _manifest("20260812-014335", ok=False, runtime="iOS 26.5"),
            _manifest("20260812-014336", ok=False, runtime="iOS 26.5"),
        ]
    )
    assert [r.device_runtime for r in records] == ["iOS 18.6"] * 2 + ["iOS 26.5"] * 2
    report = rank_flakiness(records)
    assert [s.classification for s in report.scenarios] == ["deterministic", "deterministic"]


def test_records_from_manifests_marks_a_run_spanning_os_versions_unknown() -> None:
    """The record is per run while the label is per scenario, so a mixed run speaks for no version."""
    mixed: dict[str, object] = {
        "runId": "20260812-013907",
        "ok": False,
        "provenance": {"scenarioHash": _HASH},
        "scenarios": [
            {"scenario": "login", "ok": True, "device_runtime": "iOS 18.6"},
            {"scenario": "pay", "ok": False, "device_runtime": "iOS 26.5"},
        ],
    }
    (record,) = records_from_manifests([mixed])
    # "" — determined, no single OS — not None, which would invite the hosted backfill to retry it.
    assert record.device_runtime == ""


# --- the stats aggregate, which joins durations against the same key ---


def test_stats_does_not_pool_durations_across_os_versions() -> None:
    stats = aggregate_runs(
        [
            _manifest("20260812-013907", ok=True, runtime="iOS 18.6", duration_s=1.0),
            _manifest("20260812-014335", ok=True, runtime="iOS 26.5", duration_s=9.0),
        ]
    )
    by_os = {sc.device_os.display: sc.avg_duration_s for sc in stats.scenarios if sc.device_os}
    assert by_os == {"iOS 18.6": 1.0, "iOS 26.5": 9.0}


def test_target_metrics_flaky_rate_counts_scenarios_not_per_os_series() -> None:
    """A wider device matrix must not make a target look healthier than an identical one-device one."""
    one_device = [
        _manifest("20260812-013907", ok=True, runtime="iOS 18.6"),
        _manifest("20260812-013908", ok=False, runtime="iOS 18.6"),
    ]
    # The same scenario, flaky on one OS of two, plus a run on a second OS that is consistent.
    matrix = [
        *one_device,
        _manifest("20260812-014335", ok=True, runtime="iOS 26.5"),
        _manifest("20260812-014336", ok=True, runtime="iOS 26.5"),
    ]
    assert target_metrics("one device", one_device).flaky_rate == 1.0
    assert target_metrics("device matrix", matrix).flaky_rate == 1.0
