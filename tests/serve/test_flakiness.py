"""Cross-run flakiness score over the serve DB run history (BE-0220, Half 1).

Deterministic and observational: the score reads each run's recorded verdict and provenance
stamp and ranks scenarios by how much their verdict flips at a constant content fingerprint. It
computes no pass/fail and gates nothing — it reuses `audit --history`'s exact classification
(`bajutsu.analysis.audit.classify_stability`).
"""

from datetime import UTC, datetime, timedelta

from bajutsu.serve.flakiness import FlakinessReport, rank_flakiness
from bajutsu.serve.server.db import RunRecord

_EPOCH = datetime(2026, 7, 1, tzinfo=UTC)


def _run(
    rid: str,
    *,
    ok: bool,
    scenario_hash: str | None = "h-login",
    name: str = "login",
    at: datetime | None = None,
) -> RunRecord:
    """A finished run record carrying just what the flakiness score reads."""
    return RunRecord(
        id=rid,
        org_id="o1",
        status="finished",
        ok=ok,
        created_at=at if at is not None else _EPOCH,
        summary={"scenarios": [name]},
        scenario_hash=scenario_hash,
    )


def test_flaky_when_verdict_flips_at_constant_hash() -> None:
    report = rank_flakiness([_run("r1", ok=True), _run("r2", ok=True), _run("r3", ok=False)])
    assert isinstance(report, FlakinessReport)
    (s,) = report.scenarios
    assert s.scenario_hash == "h-login"
    assert s.name == "login"
    assert (s.runs, s.passed, s.failed) == (3, 2, 1)
    assert s.classification == "flaky"
    assert s.flip_rate == 2 * 1 / 3  # 2 * min(passed, failed) / runs


def test_single_run_is_unproven() -> None:
    (s,) = rank_flakiness([_run("r1", ok=True)]).scenarios
    assert s.classification == "unproven"
    assert s.flip_rate == 0.0


def test_all_pass_is_deterministic() -> None:
    (s,) = rank_flakiness([_run("r1", ok=True), _run("r2", ok=True)]).scenarios
    assert s.classification == "deterministic"
    assert s.flip_rate == 0.0


def test_all_fail_is_deterministic() -> None:
    (s,) = rank_flakiness([_run("r1", ok=False), _run("r2", ok=False)]).scenarios
    assert s.classification == "deterministic"


def test_distinct_hashes_are_separate_scenarios() -> None:
    report = rank_flakiness(
        [
            _run("r1", ok=True, scenario_hash="h-a", name="a"),
            _run("r2", ok=False, scenario_hash="h-a", name="a"),
            _run("r3", ok=True, scenario_hash="h-b", name="b"),
            _run("r4", ok=True, scenario_hash="h-b", name="b"),
        ]
    )
    by_hash = {s.scenario_hash: s for s in report.scenarios}
    assert by_hash["h-a"].classification == "flaky"
    assert by_hash["h-b"].classification == "deterministic"


def test_runs_without_provenance_are_skipped() -> None:
    report = rank_flakiness([_run("r1", ok=True), _run("r2", ok=False, scenario_hash=None)])
    assert report.skipped == 1
    (s,) = report.scenarios
    assert s.runs == 1


def test_runs_with_null_verdict_are_skipped() -> None:
    # A queued/errored run with no recorded verdict can't contribute a pass or a fail.
    report = rank_flakiness([_run("r1", ok=True), _run("r2", ok=None)])  # type: ignore[arg-type]
    assert report.skipped == 1
    (s,) = report.scenarios
    assert s.runs == 1


def test_flaky_first_then_by_flip_rate_descending() -> None:
    report = rank_flakiness(
        [
            # h-mild: 3 pass, 1 fail -> flip_rate 0.5
            _run("m1", ok=True, scenario_hash="h-mild", name="mild"),
            _run("m2", ok=True, scenario_hash="h-mild", name="mild"),
            _run("m3", ok=True, scenario_hash="h-mild", name="mild"),
            _run("m4", ok=False, scenario_hash="h-mild", name="mild"),
            # h-bad: 1 pass, 1 fail -> flip_rate 1.0 (flakiest)
            _run("b1", ok=True, scenario_hash="h-bad", name="bad"),
            _run("b2", ok=False, scenario_hash="h-bad", name="bad"),
            # h-ok: deterministic, must rank after the flaky ones
            _run("o1", ok=True, scenario_hash="h-ok", name="ok"),
            _run("o2", ok=True, scenario_hash="h-ok", name="ok"),
        ]
    )
    order = [s.scenario_hash for s in report.scenarios]
    assert order == ["h-bad", "h-mild", "h-ok"]


def test_window_runs_keeps_only_the_newest_n_per_scenario() -> None:
    # Newest three runs all pass; the single old failure falls outside the window, so the
    # windowed view classifies the scenario deterministic even though its full history flips.
    report = rank_flakiness(
        [
            _run("old", ok=False, at=_EPOCH),
            _run("r1", ok=True, at=_EPOCH + timedelta(days=1)),
            _run("r2", ok=True, at=_EPOCH + timedelta(days=2)),
            _run("r3", ok=True, at=_EPOCH + timedelta(days=3)),
        ],
        window_runs=3,
    )
    (s,) = report.scenarios
    assert s.runs == 3
    assert s.classification == "deterministic"


def test_since_drops_older_runs() -> None:
    report = rank_flakiness(
        [
            _run("old", ok=False, at=_EPOCH),
            _run("r1", ok=True, at=_EPOCH + timedelta(days=5)),
            _run("r2", ok=True, at=_EPOCH + timedelta(days=6)),
        ],
        since=_EPOCH + timedelta(days=1),
    )
    (s,) = report.scenarios
    assert s.runs == 2
    assert s.classification == "deterministic"


def test_representative_pass_and_fail_runs_are_the_newest_of_each() -> None:
    report = rank_flakiness(
        [
            _run("p-old", ok=True, at=_EPOCH),
            _run("f-old", ok=False, at=_EPOCH + timedelta(days=1)),
            _run("p-new", ok=True, at=_EPOCH + timedelta(days=2)),
            _run("f-new", ok=False, at=_EPOCH + timedelta(days=3)),
        ]
    )
    (s,) = report.scenarios
    assert s.representative_pass_run_id == "p-new"
    assert s.representative_fail_run_id == "f-new"


def test_window_runs_zero_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="window_runs"):
        rank_flakiness([_run("r1", ok=True)], window_runs=0)


def test_window_runs_negative_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="window_runs"):
        rank_flakiness([_run("r1", ok=True)], window_runs=-1)


def test_naive_created_at_does_not_raise_with_since() -> None:
    # SQLite hands back naive datetimes for DateTime(timezone=True) columns; a tz-aware `since`
    # must not raise TypeError when compared against a naive record.created_at.
    naive_at = datetime(2026, 7, 1)  # noqa: DTZ001 — intentionally naive, simulating SQLite output
    aware_since = _EPOCH + timedelta(days=1)  # tz-aware
    record = RunRecord(
        id="r1",
        org_id="o1",
        status="finished",
        ok=True,
        created_at=naive_at,
        summary={"scenarios": ["s"]},
        scenario_hash="h1",
    )
    report = rank_flakiness([record], since=aware_since)
    # naive_at (UTC equivalent: 2026-07-01) is before since (2026-07-02), so it's dropped
    assert report.scenarios == []


def test_naive_created_at_included_when_after_since() -> None:
    naive_at = datetime(2026, 7, 10)  # noqa: DTZ001 — intentionally naive, simulating SQLite output
    aware_since = _EPOCH  # 2026-07-01 UTC
    record = RunRecord(
        id="r1",
        org_id="o1",
        status="finished",
        ok=True,
        created_at=naive_at,
        summary={"scenarios": ["s"]},
        scenario_hash="h1",
    )
    report = rank_flakiness([record], since=aware_since)
    assert len(report.scenarios) == 1
