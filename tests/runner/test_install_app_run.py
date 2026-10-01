"""Tests for running `installApp` and `setPrimaryTarget` through the pipeline (BE-0447, unit 3).

The update journey end to end on the fake path: start on the old build, install the new one over
it, move the primary, bring the new build to the front, and keep asserting on it. Each target's
lease records what happens to the shared device, so the order of installs, launches, retirements,
and releases is checkable without a Simulator.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
from _runner import _eff, _el

from bajutsu.common import backends
from bajutsu.common.config import Effective, IosConfig, require_ios
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.orchestrator import DeviceControl
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import LeaseFn, TargetPool
from bajutsu.common.scenario import Scenario


@pytest.fixture(autouse=True)
def _fake_foregrounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake driver advertises no app-lifecycle control (its environment's control is simctl's),
    # so these runs grant it here: each lease below carries its own in-memory `foreground` double.
    def caps(actuator: str, eff: Effective, udid: str = "booted") -> frozenset[str]:
        return backends.capabilities_for_run(actuator, eff, udid) | {
            base.Capability.DC_APP_LIFECYCLE
        }

    monkeypatch.setattr("bajutsu.common.runner.pipeline.capabilities_for_run", caps)


_SCREEN = [
    _el("ok", "OK", ["button"]),
    _el("popup", "Popup"),
    _el("dismiss", "Dismiss"),
    _el("row", "Row 1"),
    _el("row", "Row 2"),
]


class _Foreground:
    """A `DeviceControl` double: `foreground()` is the only operation these runs use."""

    def foreground(self) -> None:
        return None


class _Device:
    """One shared device whose leases record installs, launches, and releases in order."""

    def __init__(self, bundles: dict[str, str]) -> None:
        self.log: list[str] = []
        self.drivers: dict[str, FakeDriver] = {}
        self._names = {bundle: name for name, bundle in bundles.items()}

    def pool(self, name: str) -> LeaseFn:
        def lease(eff: Effective, scenario: Scenario) -> Lease:
            self.log.append(f"lease {name}")
            return self._lease(name, joinable=True)

        return lease

    def _lease(self, name: str, *, joinable: bool) -> Lease:
        def join(eff: Effective, scenario: Scenario, fresh: bool) -> Lease:
            member = self._names[require_ios(eff).bundle_id]
            self.log.append(f"{'join' if fresh else 'launch'} {member}")
            return self._lease(member, joinable=False)

        def install(eff: Effective, keep_data: bool) -> None:
            member = self._names[require_ios(eff).bundle_id]
            self.log.append(f"install {member} keepData={keep_data}")

        driver = FakeDriver(list(_SCREEN))
        self.drivers[name] = driver
        return Lease(
            driver=driver,
            sink=NullSink(),
            relaunch=None,
            control=cast(DeviceControl, _Foreground()),
            collector=None,
            release=lambda: self.log.append(f"release {name}"),
            join=join if joinable else None,
            install=install,
        )


def _app_eff(bundle: str) -> Effective:
    return replace(_eff(), platform_config=IosConfig(bundle_id=bundle))


_SAME_APP = {"old": "com.example.showcase", "new": "com.example.showcase"}


def _run(device: _Device, bundles: dict[str, str], data: dict[str, object]) -> list[object]:
    targets = {n: TargetPool(_app_eff(b), device.pool(n), "fake") for n, b in bundles.items()}
    scenario = Scenario.model_validate({"name": "update", **data})
    return list(run_all(_eff(), [scenario], device.pool("unused"), targets=targets))


def test_the_update_journey_runs_on_one_device() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {"tap": {"id": "ok"}},
                {"installApp": {"from": "new"}},
                {"setPrimaryTarget": {"target": "new"}},
                {"foreground": {}},
                {"tap": {"id": "ok"}},
            ],
            "expect": [{"exists": {"id": "ok"}}],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert device.log == [
        "lease old",
        "install new keepData=True",
        "launch new",
        "release new",
        "release old",
    ]
    assert ("tap", {"id": "ok"}) in device.drivers["new"].actions


def test_a_step_on_the_retired_build_fails_by_name() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {"installApp": {"from": "new", "keepData": False}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "target 'old' is retired" in (results[0].failure or "")  # type: ignore[attr-defined]
    assert "install new keepData=False" in device.log


def test_a_companion_with_its_own_identifier_retires_nothing() -> None:
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _Device(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "steps": [
                {"installApp": {"from": "auth"}},
                {"target": "auth", "foreground": {}},
                {"target": "auth", "tap": {"id": "ok"}},
                {"foreground": {}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]


def test_an_install_running_a_second_time_fails_by_name() -> None:
    # Two matches, so the loop body's install runs twice; the scenario model cannot see that.
    results = _run(
        _Device(_SAME_APP),
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [
                {
                    "forEach": {
                        "sel": {"id": "row"},
                        "as": "x",
                        "steps": [{"target": "new", "installApp": {"from": "new"}}],
                    }
                }
            ],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "it was already installed in this scenario" in (results[0].failure or "")  # type: ignore[attr-defined]


def test_an_omitted_target_interrupt_follows_a_moved_primary() -> None:
    bundles = {"app": "com.example.app", "auth": "com.example.auth"}
    device = _Device(bundles)
    results = _run(
        device,
        bundles,
        {
            "targets": [["app", "auth"]],
            "primaryTarget": "app",
            "installs": ["auth"],
            "interrupts": [
                {"condition": {"exists": {"id": "popup"}}, "steps": [{"tap": {"id": "dismiss"}}]}
            ],
            "steps": [
                {"setPrimaryTarget": {"target": "auth"}},
                {"tap": {"id": "ok"}},
            ],
        },
    )
    assert results[0].ok, results[0].failure  # type: ignore[attr-defined]
    assert ("tap", {"id": "dismiss"}) in device.drivers["auth"].actions
    assert ("tap", {"id": "dismiss"}) not in device.drivers["app"].actions


def test_an_install_the_backend_refuses_fails_its_step() -> None:
    device = _Device(_SAME_APP)

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        held = device.pool("old")(eff, scenario)

        def refuse(eff: Effective, keep_data: bool) -> None:
            raise RuntimeError("the device refused the build")

        return replace(held, install=refuse)

    targets = {n: TargetPool(_app_eff(b), lease, "fake") for n, b in _SAME_APP.items()}
    scenario = Scenario.model_validate(
        {
            "name": "update",
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [{"installApp": {"from": "new"}}],
        }
    )
    results = run_all(_eff(), [scenario], lease, targets=targets)
    assert not results[0].ok
    assert "installApp from 'new': the device refused the build" in (results[0].failure or "")


def test_an_expect_entry_on_the_retired_build_fails_by_name() -> None:
    device = _Device(_SAME_APP)
    results = _run(
        device,
        _SAME_APP,
        {
            "targets": [["old", "new"]],
            "primaryTarget": "old",
            "steps": [{"installApp": {"from": "new"}}],
            "expect": [{"exists": {"id": "ok"}}],
        },
    )
    assert not results[0].ok  # type: ignore[attr-defined]
    assert "expect: target 'old' is retired" in (results[0].failure or "")  # type: ignore[attr-defined]
