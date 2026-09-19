"""Tests for bringing several declared targets up and tearing them down together (BE-0428).

The pipeline leases one device per target a scenario declares, before its first step runs, and
releases every one of them afterwards. These cover the ordering that keeps concurrent workers from
deadlocking, the rollback when a launch fails partway through, and the per-target resolution of the
things that used to be one run-wide value: the capability preflight and the config lifecycle hooks.

No device and no Simulator: each target's lease hands back a `FakeDriver`, so the whole bring-up
path is exercised on the fast gate.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from _runner import _eff, _el, _web_eff

from bajutsu.common.assertions.evaluate.golden_context import GoldenContext
from bajutsu.common.config import Effective
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.evidence.network import Collector, NetworkExchange, ScreenTransition
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import LeaseFn, TargetPool
from bajutsu.common.scenario import Scenario

_SCREEN = [_el("ok", "OK", ["button"]), _el("other", "Other", ["button"])]


def _recording_lease(
    log: list[str],
    name: str,
    *,
    released: list[str] | None = None,
    boom: bool = False,
    collector: Collector | None = None,
) -> LeaseFn:
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
            collector=collector,
            release=lambda: (released if released is not None else []).append(name),
        )

    return lease


class _ClearTrackingCollector:
    """A `Collector` whose only interesting behavior is counting `clear()` calls (BE-0428)."""

    def __init__(self) -> None:
        self.cleared = 0

    def snapshot(self) -> list[NetworkExchange]:
        return []

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return []

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        return []

    def clear(self) -> None:
        self.cleared += 1

    def stop(self) -> None:
        pass


class _QueryFailsDriver(FakeDriver):
    """A driver whose `query()` always raises, for the golden screen-bounds probe's fallback."""

    def query(self) -> list[base.Element]:
        raise RuntimeError("device unreachable")


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


def test_redact_unions_every_declared_targets_own_config() -> None:
    # BE-0428: a value one target's config marks secret must be scrubbed everywhere, not only from
    # that one target's own capture — widening the redaction set is the safer error.
    from bajutsu.common.runner.pipeline import _union_redact
    from bajutsu.common.scenario import Redact

    app_eff = replace(_eff(), redact=Redact(labels=["app-secret"], headers=["x-app-token"]))
    web_eff = replace(_web_eff(), redact=Redact(labels=["web-secret"], headers=["x-web-token"]))
    merged = _union_redact([app_eff, web_eff])
    assert sorted(merged.labels) == ["app-secret", "web-secret"]
    assert sorted(merged.headers) == ["x-app-token", "x-web-token"]


def test_redact_unmask_opt_out_needs_every_target_to_agree() -> None:
    # A target still wanting the BE-0331 default protection must keep it for the whole run, even
    # when another declared target opted out of it for itself alone.
    from bajutsu.common.runner.pipeline import _union_redact
    from bajutsu.common.scenario import Redact

    protective = replace(_eff(), redact=Redact())  # keeps the default protection
    permissive = replace(_web_eff(), redact=Redact(unmaskSecureFields=True))
    merged = _union_redact([protective, permissive])
    assert merged.unmask_secure_fields is False  # one target still wants it masked

    both_permissive = replace(_eff(), redact=Redact(unmaskSecureFields=True))
    merged_both = _union_redact([both_permissive, permissive])
    assert merged_both.unmask_secure_fields is True  # every declared target agreed to release it


def test_redact_falls_back_to_the_run_wide_config_with_no_target_map() -> None:
    # A run whose scenarios declare no targets never builds a target map at all — this must stay
    # the plain single-`Effective` path with no behavior change.
    from bajutsu.common.runner.pipeline import _union_redact
    from bajutsu.common.scenario import Redact

    eff = replace(_eff(), redact=Redact(labels=["only-one"]))
    assert _union_redact([eff]).labels == ["only-one"]
    assert _union_redact([]).labels == []


def _device_control_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "name": "device-control",
            "targets": ["app", "remote"],
            "steps": [
                {"target": "app", "tap": {"id": "ok"}},
                {"target": "remote", "setLocation": {"lat": 1.0, "lon": 2.0}},
            ],
        }
    )


def _device_control_targets() -> dict[str, TargetPool]:
    # `xcuitest` on a real-device WebDriver endpoint loses simctl device control (BE-0238), so
    # `remote`'s own step rejects at the per-target preflight the same way
    # `test_each_targets_steps_are_preflighted_against_its_own_capabilities` exercises.
    return _pools(
        app=TargetPool(_eff(), _recording_lease([], "app"), "fake"),
        remote=TargetPool(
            replace(_eff(), backend=["ios"]),
            _recording_lease([], "remote"),
            "xcuitest",
            udid_spec="http://device-cloud.test:4723",
        ),
    )


def test_preflight_failure_reports_progress_naming_the_target() -> None:
    # The operator-facing progress line names which target rejected the scenario, the same as
    # `result.failure` does — not just that some scenario failed.
    progress: list[str] = []
    result = run_all(
        _eff(),
        [_device_control_scenario()],
        _recording_lease([], "x"),
        targets=_device_control_targets(),
        progress=progress.append,
    )[0]
    assert not result.ok
    assert any("remote" in line and "✘" in line for line in progress)


def test_trace_driver_wraps_every_declared_targets_own_driver(tmp_path: Path) -> None:
    # BE-0428: an extra target's driver must be wrapped too, or its half of a cross-platform
    # scenario's round trips would be silently missing from `driver_trace.json`.
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease([], "app"), "fake"),
        site=TargetPool(_web_eff(), _recording_lease([], "site"), "playwright"),
    )
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(
        _eff(),
        [_cross()],
        _recording_lease([], "x"),
        run_dir=run_dir,
        targets=targets,
        trace_driver=True,
    )
    assert results[0].ok, results[0].failure
    trace_path = run_dir / results[0].sid / "driver_trace.json"
    doc = json.loads(trace_path.read_text(encoding="utf-8"))
    driver_records = [r for r in doc["records"] if r["category"] == "driver"]
    # One `tap` per target's own step (BE-0428): only the primary's would show up if the extra
    # target's driver were never wrapped in a `TracingDriver`.
    assert sum(1 for r in driver_records if r["name"] == "tap") == 2


def test_a_multi_target_scenario_clears_every_targets_own_collector(tmp_path: Path) -> None:
    # BE-0428: network collection is cleared per scenario so one scenario's traffic never leaks
    # into the next one's evidence — an extra target's own collector needs the same reset the
    # primary's already gets.
    extra_collector = _ClearTrackingCollector()
    targets = _pools(
        app=TargetPool(_eff(), _recording_lease([], "app"), "fake"),
        site=TargetPool(
            _web_eff(), _recording_lease([], "site", collector=extra_collector), "playwright"
        ),
    )
    result = run_all(_eff(), [_cross()], _recording_lease([], "x"), targets=targets)[0]
    assert result.ok, result.failure
    assert extra_collector.cleared == 1


def test_release_all_warns_but_still_releases_every_other_lease(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # BE-0428: one target's teardown fault must not strand another target's device — the same rule
    # `guarded_teardown` applies inside the pool.
    from bajutsu.common.runner.pipeline import _release_all

    released: list[str] = []

    def _boom() -> None:
        raise RuntimeError("device gone")

    leases = {
        "a": Lease(
            driver=FakeDriver([]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=_boom,
        ),
        "b": Lease(
            driver=FakeDriver([]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append("b"),
        ),
    }
    with caplog.at_level(logging.WARNING):
        _release_all(leases)
    assert released == ["b"]
    assert any("releasing target" in r.message for r in caplog.records)


def test_golden_with_screen_falls_back_when_the_bounds_probe_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Best-effort (BE-0006): a `query()` fault while probing screen bounds for golden framing must
    # not block a scenario that asserts no goldens at all, so the caller's own context comes back
    # unchanged instead of raising.
    from bajutsu.common.runner.pipeline import _golden_with_screen

    gc = GoldenContext(goldens_dir=Path("goldens"))
    with caplog.at_level(logging.DEBUG):
        result = _golden_with_screen(gc, _QueryFailsDriver())
    assert result is gc
    assert any("golden framing failed" in r.message for r in caplog.records)


def test_golden_with_screen_is_a_noop_once_the_screen_is_already_known() -> None:
    # The probe only runs when the caller's own `GoldenContext` has no screen yet — one already
    # carrying `screen` (set by an earlier target, or passed in directly) must never be probed.
    from bajutsu.common.runner.pipeline import _golden_with_screen

    gc = GoldenContext(goldens_dir=Path("goldens"), screen=(0.0, 0.0, 100.0, 200.0))
    assert _golden_with_screen(gc, _QueryFailsDriver()) is gc
