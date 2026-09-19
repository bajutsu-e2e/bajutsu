"""Tests for bringing several declared targets up and tearing them down together (BE-0428).

The pipeline leases one device per target a scenario declares, before its first step runs, and
releases every one of them afterwards. These cover the ordering that keeps concurrent workers from
deadlocking, the rollback when a launch fails partway through, and the per-target resolution of the
things that used to be one run-wide value: the capability preflight and the config lifecycle hooks.

No device and no Simulator: each target's lease hands back a `FakeDriver`, so the whole bring-up
path is exercised on the fast gate.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from _runner import _eff, _el, _web_eff

from bajutsu.common.config import Effective
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import TargetPool
from bajutsu.common.scenario import Scenario

_SCREEN = [_el("ok", "OK", ["button"]), _el("other", "Other", ["button"])]


def _recording_lease(
    log: list[str], name: str, *, released: list[str] | None = None, boom: bool = False
):
    """A lease callable that records the order targets are leased in, and optionally fails."""

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        log.append(name)
        if boom:
            raise RuntimeError(f"{name} failed to launch")
        return Lease(
            driver=FakeDriver(list(_SCREEN)),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: (released if released is not None else []).append(name),
        )

    return lease


def _pools(**entries: TargetPool) -> dict[str, TargetPool]:
    return dict(entries)


def _cross(name: str = "cross", targets: tuple[str, str] = ("app", "site")) -> Scenario:
    return Scenario.model_validate(
        {
            "name": name,
            "targets": list(targets),
            "steps": [
                {"target": targets[0], "tap": {"id": "ok"}},
                {"target": targets[1], "tap": {"id": "other"}},
            ],
        }
    )


def test_every_declared_target_is_leased_before_the_first_step() -> None:
    # A step naming a target expects it already live — never launched lazily on first reference —
    # so an interleaved scenario never pauses mid-run to bring a platform up.
    order: list[str] = []
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease(order, "app"), "fake"),
        site=TargetPool(_web_eff(), _recording_lease(order, "site"), "playwright"),
    )
    results = run_all(_eff(), [_cross()], _recording_lease(order, "primary"), targets=targets)
    assert results[0].ok, results[0].failure
    assert sorted(order) == ["app", "site"]  # the run's own `lease` is never used
    assert "primary" not in order


def test_pools_are_acquired_in_one_fixed_order_whatever_the_declaration_order() -> None:
    # The lock-ordering discipline: every worker approaches every pool in the same order — by the
    # pool's actuator, then the target name — so no two can hold each other's next pool. Two
    # scenarios declaring the same pair in opposite order must still lease it the same way round.
    first: list[str] = []
    second: list[str] = []
    targets_first = _pools(
        app=TargetPool(_eff(), _recording_lease(first, "app"), "fake"),
        site=TargetPool(_web_eff(), _recording_lease(first, "site"), "playwright"),
    )
    targets_second = _pools(
        app=TargetPool(_eff(), _recording_lease(second, "app"), "fake"),
        site=TargetPool(_web_eff(), _recording_lease(second, "site"), "playwright"),
    )
    run_all(
        _eff(),
        [_cross(targets=("app", "site"))],
        _recording_lease(first, "x"),
        targets=targets_first,
    )
    run_all(
        _eff(),
        [_cross(targets=("site", "app"))],
        _recording_lease(second, "x"),
        targets=targets_second,
    )
    # "fake" sorts before "playwright", so both scenarios lease `app` first however they declared
    # them — the property that rules out the circular wait two opposite orders would allow.
    assert first == ["app", "site"]
    assert second == ["app", "site"]


def test_a_launch_failure_tears_down_every_target_that_did_start() -> None:
    # A partial set must never be left running: the second target's launch failing has to release
    # the first target's device, not leak it for the rest of the run.
    released: list[str] = []
    order: list[str] = []
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease(order, "app", released=released), "fake"),
        site=TargetPool(
            _web_eff(), _recording_lease(order, "site", released=released, boom=True), "playwright"
        ),
    )
    with pytest.raises(RuntimeError, match="site failed to launch"):
        run_all(_eff(), [_cross()], _recording_lease(order, "x"), targets=targets)
    assert order == ["app", "site"]  # `app` was already up when `site` failed
    assert released == ["app"]  # and it was handed back


def test_every_targets_lease_is_released_when_the_scenario_ends() -> None:
    # The run's own end brackets the whole set the same way one launch already brackets one
    # scenario, so a suite's second scenario never inherits the first's live drivers.
    released: list[str] = []
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease([], "app", released=released), "fake"),
        site=TargetPool(_web_eff(), _recording_lease([], "site", released=released), "playwright"),
    )
    run_all(_eff(), [_cross()], _recording_lease([], "x"), targets=targets)
    assert sorted(released) == ["app", "site"]


def test_each_targets_steps_are_preflighted_against_its_own_capabilities() -> None:
    # BE-0082's fail-fast preflight, per target: a construct only one declared target's backend
    # lacks must fail the scenario before any device is leased, and must name which target it is.
    leased: list[str] = []
    # `xcuitest` on a real-device WebDriver endpoint loses simctl device control (BE-0238), while
    # the fake backend keeps everything — so an `app` step needing device control is supported and
    # the same step on `remote` is not.
    scenario = Scenario.model_validate(
        {
            "name": "device-control",
            "targets": ["app", "remote"],
            "steps": [
                {"target": "app", "tap": {"id": "ok"}},
                {"target": "remote", "setLocation": {"lat": 1.0, "lon": 2.0}},
            ],
        }
    )
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease(leased, "app"), "fake"),
        remote=TargetPool(
            replace(_eff(), backend=["ios"]),
            _recording_lease(leased, "remote"),
            "xcuitest",
            udid_spec="http://device-cloud.test:4723",
        ),
    )
    result = run_all(_eff(), [scenario], _recording_lease(leased, "x"), targets=targets)[0]
    assert not result.ok
    assert result.failure is not None
    assert "remote" in result.failure
    assert leased == []  # rejected before any device was leased


def test_a_multi_target_run_reports_one_device_row_per_target() -> None:
    # `RunResult`'s singular device fields describe exactly one target, so a multi-target run
    # leaves them empty and records one row per declared target instead — rather than presenting
    # one target's values as if they spoke for the whole scenario.
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease([], "app"), "fake"),
        site=TargetPool(_web_eff(), _recording_lease([], "site"), "playwright"),
    )
    result = run_all(_eff(), [_cross()], _recording_lease([], "x"), targets=targets)[0]
    assert result.device == ""
    assert result.device_name == ""
    assert result.backend == ""
    assert sorted(result.target_devices) == ["app", "site"]
    assert result.target_devices["site"].backend == "playwright"


def test_a_single_target_run_keeps_todays_singular_device_fields() -> None:
    # The other half of the same convention: nothing changes for a scenario declaring no targets,
    # so an existing JUnit/CTRF reader sees exactly what it always has.
    scenario = Scenario.model_validate({"name": "legacy", "steps": [{"tap": {"id": "ok"}}]})
    result = run_all(_eff(), [scenario], _recording_lease([], "only"))[0]
    assert result.target_devices == {}
