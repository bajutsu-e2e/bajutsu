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
    XcuitestEnvironment,
    environment_for,
)
from bajutsu.scenario import Preconditions


def test_environment_for_selects_by_actuator() -> None:
    assert isinstance(environment_for("idb", "UDID"), IosEnvironment)
    assert isinstance(environment_for("xcuitest", "UDID"), XcuitestEnvironment)
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


# --- The crawl-facing seams the CLI used to branch on `actuator == "playwright"` for (Slice 3) --- #


def test_crawl_health_seams_are_web_only() -> None:
    # The crawl's web crash signal, dialog auto-clear, and wedged-browser recovery exist only on web;
    # the device backends read the accessibility tree (engine default) and have no such seams. The CLI
    # now asks the Environment for these instead of naming the actuator.
    web = WebEnvironment("playwright")
    assert web.crawl_aliveness() is not None
    assert web.crawl_recover() is not None
    assert web.crawl_dialog_clearer() is not None
    for device in (IosEnvironment("idb", "U"), FakeEnvironment("fake", "U")):
        assert device.crawl_aliveness() is None
        assert device.crawl_recover() is None
        assert device.crawl_dialog_clearer() is None


def test_web_crawl_seams_drive_the_web_driver() -> None:
    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.relaunched = 0
            self.errored = False

        def pop_dialogs(self) -> list[str]:
            return ["alert: hi"]

        def relaunch(self) -> None:
            self.relaunched += 1

        def pop_page_errors(self) -> list[str]:
            return ["boom"] if self.errored else []

        def last_nav_status(self) -> int | None:
            return 200

    web = WebEnvironment("playwright")
    driver = _WebDriver()

    clear = web.crawl_dialog_clearer()
    assert clear is not None
    assert clear(driver) == ["alert: hi"]  # type: ignore[arg-type]  # delegates to pop_dialogs

    recover = web.crawl_recover()
    assert recover is not None
    recover(driver)  # type: ignore[arg-type]
    assert driver.relaunched == 1  # delegates to the driver's relaunch

    alive = web.crawl_aliveness()
    assert alive is not None
    element: base.Element = {
        "identifier": "root",
        "label": None,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 1.0, 1.0),
    }
    assert alive(driver, [element]) is True  # no page error, 2xx nav, a rendered element → alive
    driver.errored = True
    assert alive(driver, [element]) is False  # a JS error → not alive (delegates to web_is_alive)


def test_web_crawl_reset_makes_a_fresh_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # The web "reset to a clean start" is a fresh BrowserContext (the erase equivalent), then a
    # readiness wait. (_await_ready is stubbed so the test doesn't poll a fake driver.)
    monkeypatch.setattr("bajutsu.environment._await_ready", lambda *a, **k: None)

    class _WebDriver:
        name = "web"

        def __init__(self) -> None:
            self.reset = 0

        def reset_context(self) -> None:
            self.reset += 1

    driver = _WebDriver()
    WebEnvironment("playwright").crawl_reset(_eff())(driver)  # type: ignore[arg-type]
    assert driver.reset == 1


def test_device_crawl_reset_relaunches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # The device "reset" is a relaunch (terminate then launch), not a full erase — fast per visit.
    monkeypatch.setattr("bajutsu.environment._await_ready", lambda *a, **k: None)
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    from bajutsu.drivers.fake import FakeDriver

    IosEnvironment("idb", "U-1", env_run=fake_run).crawl_reset(_eff())(FakeDriver([]))
    assert ["xcrun", "simctl", "terminate", "U-1", "com.example.demo"] in calls
    assert any(a[:3] == ["xcrun", "simctl", "launch"] and "U-1" in a for a in calls)


def test_plan_lanes_sizes_web_and_device_pools() -> None:
    # Web has no devices, so the worker count alone sizes the browser-lane set; a device pool resolves
    # the --udid list and caps the workers to it. The CLI no longer special-cases the web string.
    web = WebEnvironment("playwright")
    assert web.plan_lanes("booted", 3) == ["web", "web", "web"]
    assert web.plan_lanes("booted", 0) == ["web"]  # at least one lane

    device = IosEnvironment("idb", "")  # explicit udids resolve to themselves (no simctl call)
    assert device.plan_lanes("U1,U2", 5) == ["U1", "U2"]  # capped to the pool
    assert device.plan_lanes("U1,U2,U3", 2) == ["U1", "U2"]  # capped to the workers


def test_only_device_platforms_have_devices() -> None:
    assert WebEnvironment("playwright").has_devices() is False
    assert IosEnvironment("idb", "U").has_devices() is True
    assert FakeEnvironment("fake", "U").has_devices() is True


# --- BE-0019: XcuitestEnvironment ---


def test_xcuitest_environment_requires_test_runner_in_config() -> None:
    xe = XcuitestEnvironment("xcuitest", "UDID", env_run=lambda a, extra_env=None: "")
    with pytest.raises(env.DeviceError, match="testRunner"):
        xe.start(_eff(), Preconditions())


def test_xcuitest_environment_start_launches_runner_and_creates_driver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from dataclasses import replace as dc_replace

    from bajutsu.config import XcuitestConfig

    simctl_calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        simctl_calls.append(args)
        return ""

    monkeypatch.setattr("bajutsu.environment._allocate_port", lambda: 54321)

    popen_calls: list[dict[str, object]] = []

    class FakePopen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            popen_calls.append({"cmd": cmd, "kwargs": kwargs})

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    class FakeXcuitestDriver:
        name = "xcuitest"
        ready_called = False

        def await_ready(self, **kw: object) -> None:
            self.ready_called = True

        def query(self) -> list[base.Element]:
            return []

        def capabilities(self) -> set[str]:
            return set()

    fake_driver = FakeXcuitestDriver()
    make_driver_calls: list[dict[str, object]] = []

    def mock_make_driver(*a: object, **k: object) -> FakeXcuitestDriver:
        make_driver_calls.append({"args": a, "kwargs": k})
        return fake_driver

    monkeypatch.setattr("bajutsu.environment.make_driver", mock_make_driver)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".xctestrun") as f:
        eff = dc_replace(_eff(), xcuitest=XcuitestConfig(test_runner=f.name), app_path=None)
        xe = XcuitestEnvironment("xcuitest", "UDID-1", env_run=fake_run)
        driver = xe.start(eff, Preconditions())

    assert driver is fake_driver
    assert fake_driver.ready_called
    assert len(popen_calls) == 1
    assert popen_calls[0]["cmd"][0] == "xcodebuild"
    assert "UDID-1" in str(popen_calls[0]["cmd"])
    popen_env = popen_calls[0]["kwargs"].get("env", {})
    assert isinstance(popen_env, dict) and popen_env.get("BAJUTSU_RUNNER_PORT") == "54321"
    assert make_driver_calls[0]["kwargs"].get("runner_port") == 54321


def test_xcuitest_environment_teardown_stops_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    terminated = []

    class FakeProc:
        def terminate(self) -> None:
            terminated.append(True)

        def wait(self, timeout: float | None = None) -> int:
            return 0

    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    xe = XcuitestEnvironment("xcuitest", "UDID-1", env_run=fake_run)
    xe._runner_proc = FakeProc()  # type: ignore[assignment]

    from bajutsu.drivers.fake import FakeDriver

    xe.teardown(FakeDriver([]), _eff())
    assert len(terminated) == 1
    assert ["xcrun", "simctl", "terminate", "UDID-1", "com.example.demo"] in calls
