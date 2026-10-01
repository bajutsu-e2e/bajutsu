"""Tests for leasing one device per device group and joining its members to it (BE-0447).

A device group's members share one device: the pipeline leases it once, for the group's last
starting member, and the others join that lease in reverse declared order, so the first starting
member is in front when the first step runs. A later member joins nobody at the start. Every
member's app stops before the device goes back to the pool. The pool's own `join` brings a member
up through the environment's `start_member`; the fake backend implements it, so all of this runs on
the fast gate.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _runner import _eff, _el, _web_eff

from bajutsu.common.config import AndroidConfig, Effective, IosConfig, require_ios
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.evidence.network import NetworkExchange, ScreenTransition
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.platform_lifecycle.environments.web import WebEnvironment
from bajutsu.common.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.common.runner import Lease, device_pool, run_all
from bajutsu.common.runner.types import LeaseFn, TargetPool
from bajutsu.common.scenario import Preconditions, Scenario

_SCREEN = [_el("ok", "OK", ["button"]), _el("other", "Other", ["button"])]
_EXCHANGE = NetworkExchange(method="GET", path="/a")


def _app_eff(bundle: str) -> Effective:
    return replace(_eff(), platform_config=IosConfig(bundle_id=bundle))


class _Device:
    """One shared device: records every lease, join, and release in order."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def pool(self, name: str) -> LeaseFn:
        def lease(eff: Effective, scenario: Scenario) -> Lease:
            self.log.append(f"lease {name}")
            return self._lease(name)

        return lease

    def _lease(self, name: str, *, joinable: bool = True) -> Lease:
        def join(eff: Effective, scenario: Scenario, install: bool) -> Lease:
            member = _NAMES[require_ios(eff).bundle_id]
            self.log.append(f"join {member}" if install else f"launch {member}")
            return self._lease(member, joinable=False)

        return Lease(
            driver=FakeDriver(list(_SCREEN)),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: self.log.append(f"release {name}"),
            join=join if joinable else None,
        )


_NAMES = {"com.example.app": "app", "com.example.auth": "auth", "com.example.beta": "beta"}


def _pools(device: _Device, *names: str) -> dict[str, TargetPool]:
    return {n: TargetPool(_app_eff(f"com.example.{n}"), device.pool(n), "fake") for n in names}


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate({"name": "group", **data})


def test_a_group_leases_once_and_its_members_join_in_reverse_order() -> None:
    device = _Device()
    s = _scenario(
        {
            "targets": [["app", "auth", "beta"]],
            "primaryTarget": "app",
            "installs": ["auth", "beta"],
            "steps": [{"tap": {"id": "ok"}}, {"target": "auth", "tap": {"id": "other"}}],
        }
    )
    results = run_all(_eff(), [s], device.pool("unused"), targets=_pools(device, *_NAMES.values()))
    assert results[0].ok, results[0].failure
    # One lease for the device, taken by the last starting member, then the others join so the
    # primary comes up last and is in front; teardown stops every joined app before the device.
    assert device.log == [
        "lease beta",
        "join auth",
        "join app",
        "release app",
        "release auth",
        "release beta",
    ]


def test_a_later_member_is_not_brought_up_at_the_start() -> None:
    device = _Device()
    s = _scenario(
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "steps": [{"tap": {"id": "ok"}}],
        }
    )
    results = run_all(_eff(), [s], device.pool("unused"), targets=_pools(device, "app", "auth"))
    assert results[0].ok, results[0].failure
    assert device.log == ["lease app", "release app"]


def test_a_step_addressed_to_a_later_member_fails_by_name() -> None:
    device = _Device()
    s = _scenario(
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "steps": [{"target": "auth", "tap": {"id": "ok"}}],
        }
    )
    results = run_all(_eff(), [s], device.pool("unused"), targets=_pools(device, "app", "auth"))
    assert not results[0].ok
    assert "target 'auth' is not installed yet" in (results[0].failure or "")
    assert device.log == ["lease app", "release app"]


def test_two_groups_lease_one_device_each() -> None:
    device = _Device()
    s = _scenario(
        {
            "targets": [["app", "auth"], "beta"],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"tap": {"id": "ok"}}, {"target": "beta", "tap": {"id": "ok"}}],
        }
    )
    results = run_all(_eff(), [s], device.pool("unused"), targets=_pools(device, *_NAMES.values()))
    assert results[0].ok, results[0].failure
    assert [e for e in device.log if not e.startswith("release")] == [
        "lease auth",
        "join app",
        "lease beta",
    ]


def test_a_lease_that_cannot_host_a_second_app_fails_loudly() -> None:
    def lease(eff: Effective, scenario: Scenario) -> Lease:
        return Lease(
            driver=FakeDriver(list(_SCREEN)),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
        )

    s = _scenario(
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"tap": {"id": "ok"}}],
        }
    )
    targets = {n: TargetPool(_app_eff(f"com.example.{n}"), lease, "fake") for n in ("app", "auth")}
    # A wiring defect, not an authoring mistake: the caller handed the pipeline a lease with no way
    # to start a second app, so the run stops rather than drive both members through one app.
    with pytest.raises(RuntimeError, match="cannot host a second app"):
        run_all(_eff(), [s], lease, targets=targets)


def test_preflight_refuses_a_group_on_a_backend_that_cannot_share_a_device() -> None:
    device = _Device()
    s = _scenario(
        {
            "targets": [["site", "other"]],
            "primaryTarget": "site",
            "installs": ["other"],
            "steps": [{"tap": {"id": "ok"}}],
        }
    )
    targets = {n: TargetPool(_web_eff(), device.pool(n), "playwright") for n in ("site", "other")}
    results = run_all(_eff(), [s], device.pool("unused"), targets=targets)
    assert not results[0].ok
    assert "a device group" in (results[0].failure or "")
    assert device.log == []  # refused before any device was leased


# --- the pool's own join ---------------------------------------------------------------------


def test_the_pool_joins_a_member_on_the_same_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bajutsu.common.backends.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    lease, shutdown = device_pool(
        ["UDID-A"], ["fake"], _eff(), Path("runs"), available=lambda b: True
    )
    scn = _scenario({"steps": [{"tap": {"id": "ok"}}]})
    try:
        anchor = lease(_app_eff("com.example.app"), scn)
        assert anchor.join is not None
        member = anchor.join(_app_eff("com.example.auth"), scn, True)
        assert member.udid == anchor.udid == "UDID-A"
        # Device-scoped recovery reads the device the anchor holds, so a crash retry judged on a
        # member still escalates and still finds the runner's evidence.
        assert member.request_device_replacement == anchor.request_device_replacement
        assert member.crash_artifacts() == anchor.crash_artifacts()
        assert member.join is None  # only the device's own lease hosts more apps
        assert member.driver is not anchor.driver
        member.release()
        anchor.release()
        # The device went back to the pool through the anchor alone, so it can be leased again.
        again = lease(_app_eff("com.example.app"), scn)
        again.release()
    finally:
        shutdown()


def test_a_member_whose_app_never_gets_ready_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bajutsu.common.backends.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    ended: list[str] = []
    monkeypatch.setattr(
        "bajutsu.common.platform_lifecycle.environments.fake.FakeEnvironment.end_member",
        lambda self, driver, eff: ended.append(require_ios(eff).bundle_id),
    )
    lease, shutdown = device_pool(
        ["UDID-A"], ["fake"], _eff(), Path("runs"), available=lambda b: True
    )
    scn = _scenario({"steps": [{"tap": {"id": "ok"}}]})
    try:
        anchor = lease(_app_eff("com.example.app"), scn)
        assert anchor.join is not None

        def never_ready(*args: object, **kwargs: object) -> object:
            raise TimeoutError("not ready")

        monkeypatch.setattr("bajutsu.common.runner.pool.await_ready", never_ready)
        with pytest.raises(TimeoutError):
            anchor.join(_app_eff("com.example.auth"), scn, True)
        assert ended == ["com.example.auth"]
        anchor.release()
    finally:
        shutdown()


# --- a later member's activation ------------------------------------------------------------


def test_activating_a_later_member_joins_its_groups_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing in this unit marks a member installed (the `installApp` step is BE-0447 unit 3), so
    # the roster's activation is driven directly: it must join the device the group's lease holds,
    # and the scenario's teardown must stop that member's app before the device goes back.
    def activating(*args: object, **kwargs: object) -> object:
        roster = kwargs["roster"]
        assert roster is not None and roster.activate is not None  # type: ignore[attr-defined]
        runtime = roster.activate("auth")  # type: ignore[attr-defined]
        assert isinstance(runtime.driver, FakeDriver)
        return run_scenario(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("bajutsu.common.runner.pipeline.run_scenario", activating)
    device = _Device()
    s = _scenario(
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "steps": [{"tap": {"id": "ok"}}],
        }
    )
    results = run_all(_eff(), [s], device.pool("unused"), targets=_pools(device, "app", "auth"))
    assert results[0].ok, results[0].failure
    # Launch only: the later member's build came from its `installApp`, never reinstalled here.
    assert device.log == ["lease app", "launch auth", "release auth", "release app"]


# --- the environments' own answer ------------------------------------------------------------


def test_the_simctl_family_refuses_a_member_and_stops_one_by_bundle() -> None:
    calls: list[list[str]] = []

    def run(args: list[str], env: object = None) -> str:
        calls.append(args)
        return ""

    env = XcuitestEnvironment("xcuitest", "UDID-1", run)
    with pytest.raises(base.UnsupportedAction, match="device groups are not supported"):
        env.start_member(_eff(), Preconditions())
    env.end_member(FakeDriver([]), _app_eff("com.example.auth"))
    assert ["xcrun", "simctl", "terminate", "UDID-1", "com.example.auth"] in calls


def test_the_web_environment_refuses_a_member() -> None:
    env = WebEnvironment("playwright")
    with pytest.raises(base.UnsupportedAction, match="cannot share a device"):
        env.start_member(_web_eff(), Preconditions())
    env.end_member(FakeDriver([]), _web_eff())  # no member ever started, so nothing to stop


def test_the_android_environment_refuses_a_member_and_stops_one_by_package() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    eff = replace(_eff(), platform_config=AndroidConfig(package="com.example.auth"))
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=run)
    with pytest.raises(base.UnsupportedAction, match="device groups are not supported"):
        env.start_member(eff, Preconditions())
    env.end_member(FakeDriver([]), eff)
    assert any("force-stop" in argv and "com.example.auth" in argv for argv in calls)


def test_a_groups_shared_collector_is_written_once(tmp_path: Path) -> None:
    # One device, one receiver: the members' traffic is the same capture, so it lands in one
    # `network.json`, not one per member that would each claim every request.
    collector = _OneCollector()

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        def join(eff: Effective, scenario: Scenario, install: bool) -> Lease:
            return replace(anchor, join=None, release=lambda: None)

        anchor = Lease(
            driver=FakeDriver(list(_SCREEN)),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=collector,
            release=lambda: None,
            join=join,
        )
        return anchor

    s = _scenario(
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "steps": [{"tap": {"id": "ok"}}],
        }
    )
    targets = {n: TargetPool(_app_eff(f"com.example.{n}"), lease, "fake") for n in ("app", "auth")}
    results = run_all(_eff(), [s], lease, targets=targets, run_dir=tmp_path)
    assert results[0].ok, results[0].failure
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("network.json"))
    assert len(written) == 1, written


class _OneCollector:
    """A `Collector` holding one exchange, so a written `network.json` is never empty."""

    def snapshot(self) -> list[NetworkExchange]:
        return [_EXCHANGE]

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return [(_EXCHANGE, 0.0)]

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        return []

    def clear(self) -> None:
        pass

    def stop(self) -> None:
        pass
