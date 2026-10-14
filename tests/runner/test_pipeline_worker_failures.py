"""A scenario the worker capability check failed is reported, never leased (BE-0450)."""

from __future__ import annotations

from pathlib import Path

from _runner import _eff, _lease

from bajutsu.common.cancellation import CANCELLED_FAILURE
from bajutsu.common.runner import run_all
from bajutsu.common.runner.types import Lease
from bajutsu.common.scenario import Scenario

_FAILURE = "worker-capability-unsupported: xcuitest runs only on macOS; this worker runs on linux"


def _scenarios(*names: str) -> list[Scenario]:
    return [Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in names]


def test_a_worker_failure_is_reported_in_place_and_never_leased() -> None:
    leased: list[str] = []

    def counting_lease(eff: object, scenario: Scenario) -> Lease:
        leased.append(scenario.name)
        return _lease(eff, scenario)  # type: ignore[arg-type]

    results = run_all(
        _eff(), _scenarios("a", "b", "c"), counting_lease, worker_failures={1: _FAILURE}
    )
    assert [r.scenario for r in results] == ["a", "b", "c"]
    assert [r.ok for r in results] == [True, False, True]
    assert results[1].failure == _FAILURE
    assert results[1].steps == []
    assert results[1].sid == "01-b"
    assert leased == ["a", "c"]


def test_a_failed_scenario_folds_in_no_other_targets_hooks() -> None:
    from dataclasses import replace

    from bajutsu.common.runner.pipeline import with_lifecycle_phases
    from bajutsu.common.scenario import Step

    base = _eff()
    prelude = [Step.model_validate({"tap": {"id": "prelude"}})]
    eff = replace(base, run_defaults=replace(base.run_defaults, setup=prelude))
    scenarios = [*_scenarios("ok"), _scenarios("failed")[0]]
    folded = with_lifecycle_phases(eff, scenarios, skip={1})
    assert len(folded[0].setup) == 1
    assert folded[1].setup == []


def test_a_worker_failure_keeps_its_reason_under_a_cancel() -> None:
    results = run_all(
        _eff(), _scenarios("a", "b"), _lease, worker_failures={1: _FAILURE}, cancelled=lambda: True
    )
    assert results[1].failure == _FAILURE


def test_a_skipped_matrix_pass_keeps_the_worker_failure(tmp_path: Path) -> None:
    from bajutsu.common.runner import run_matrix_and_report

    results = run_matrix_and_report(
        _eff(),
        _scenarios("a", "b"),
        ["chromium"],
        lambda engine, run_dir: [],  # never reached: cancelled() is already True
        tmp_path / "runs",
        "run1",
        cancelled=lambda: True,
        worker_failures={1: _FAILURE},
    )[0]
    assert [r.failure for r in results] == [CANCELLED_FAILURE, _FAILURE]
