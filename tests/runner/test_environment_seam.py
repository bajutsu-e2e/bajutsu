"""Tests for the per-platform Environment seam (BE-0009 Phase 0).

The iOS simctl sequence is exercised through `launch_driver` in test_launch.py; these cover the
seam itself: the factory's actuator→Environment selection and the web/fake lifecycles that the
single-fork refactor folded behind the Protocol.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from _runner import _eff

from bajutsu import env
from bajutsu.drivers import base
from bajutsu.environment import (
    FakeEnvironment,
    IosEnvironment,
    WebEnvironment,
    environment_for,
)
from bajutsu.scenario import Preconditions


def test_environment_for_selects_by_actuator() -> None:
    assert isinstance(environment_for("idb", "UDID"), IosEnvironment)
    assert isinstance(environment_for("playwright", "UDID"), WebEnvironment)
    assert isinstance(environment_for("fake", "UDID"), FakeEnvironment)


def test_web_environment_requires_base_url() -> None:
    eff = replace(_eff(), base_url=None)
    with pytest.raises(env.DeviceError, match="baseUrl"):
        WebEnvironment("playwright").start(eff, Preconditions())


def test_web_environment_navigates_then_returns_the_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.navigated = False

        def navigate(self) -> None:
            self.navigated = True

        def query(self) -> list[base.Element]:
            return []

    web = _WebDriver()
    monkeypatch.setattr("bajutsu.environment.make_driver", lambda *a, **k: web)
    eff = replace(_eff(), base_url="https://app.test")
    driver = WebEnvironment("playwright").start(eff, Preconditions())
    assert driver is web
    assert web.navigated is True  # the web "launch" is navigate()


def test_fake_environment_runs_no_lifecycle() -> None:
    # No device lifecycle (no env_run, no simctl): it just yields the fake driver.
    driver = FakeEnvironment("fake", "UDID").start(_eff(), Preconditions())
    assert driver.query() == []  # the real fake driver, constructed without any device step


def test_ios_environment_surfaces_a_failing_step_as_device_error() -> None:
    import subprocess

    def fake_run(args: list[str], extra_env: object = None) -> str:
        if args[:3] == ["xcrun", "simctl", "erase"]:
            raise subprocess.CalledProcessError(1, args, output="", stderr="boom")
        return ""

    with pytest.raises(env.DeviceError):
        IosEnvironment("idb", "UDID", env_run=fake_run).start(_eff(), Preconditions(erase=True))


# --- The lease-shaping capabilities the pool used to branch on `is_web` for (BE-0009 Slice 2) --- #


def test_network_observation_strategy_is_per_platform() -> None:
    # Web observes by hooking the live driver/page; the device backends use an external receiver the
    # app reports to. This capability — not the actuator name — is what the pool branches on now.
    assert WebEnvironment("playwright").observes_network_via_driver() is True
    assert IosEnvironment("idb", "UDID").observes_network_via_driver() is False
    assert FakeEnvironment("fake", "UDID").observes_network_via_driver() is False


def test_only_web_records_video_up_front() -> None:
    # Playwright records at context-creation, so the dir must exist before launch; simctl records on
    # demand, so the device backends need no up-front dir.
    assert WebEnvironment("playwright").records_video_up_front() is True
    assert IosEnvironment("idb", "UDID").records_video_up_front() is False
    assert FakeEnvironment("fake", "UDID").records_video_up_front() is False


def test_device_catalog_is_empty_for_web_and_read_for_devices() -> None:
    assert WebEnvironment("playwright").device_catalog() == {}

    import json

    catalog = json.dumps(
        {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-17-2": [{"udid": "U", "name": "X"}]}}
    )
    seen = IosEnvironment(
        "idb",
        "U",
        env_run=lambda args, extra_env=None: catalog if args == env.list_devices_cmd() else "",
    ).device_catalog()
    assert seen.get("U") == {"name": "X", "runtime": "iOS 17.2"}


def test_web_hook_collector_wires_the_scenarios_mocks() -> None:
    from bajutsu.scenario import Scenario

    class _Collector:
        def snapshot(self) -> list[object]:
            return []

        def snapshot_timed(self) -> list[object]:
            return []

        def clear(self) -> None:
            pass

        def stop(self) -> None:
            pass

    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.mocks: object = "unset"

        def network_collector(self, mocks: object = None) -> _Collector:
            self.mocks = mocks
            return _Collector()

    driver = _WebDriver()
    scn = Scenario.model_validate(
        {"name": "a", "mocks": [{"match": {"path": "/x"}}], "steps": [{"tap": {"id": "ok"}}]}
    )
    collector = WebEnvironment("playwright").hook_collector(driver, scn)  # type: ignore[arg-type]
    assert isinstance(collector, _Collector)
    assert driver.mocks == scn.mocks  # this scenario's mocks reached the page hook


def test_web_controller_is_none_and_teardown_closes_the_browser() -> None:
    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    web = WebEnvironment("playwright")
    assert web.controller(_eff()) is None  # no simctl device control on web
    driver = _WebDriver()
    web.teardown(driver, _eff())  # type: ignore[arg-type]
    assert driver.closed == 1  # release tears the browser down


def test_device_teardown_terminates_the_app() -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    from bajutsu.drivers.fake import FakeDriver

    IosEnvironment("idb", "UDID-1", env_run=fake_run).teardown(FakeDriver([]), _eff())
    assert ["xcrun", "simctl", "terminate", "UDID-1", "com.example.demo"] in calls
