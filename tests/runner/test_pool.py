"""Tests for the device pool and the per-device relauncher."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from _runner import _eff, _el

from bajutsu import env
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.network import NetworkExchange
from bajutsu.runner import (
    device_pool,
    device_relauncher,
)
from bajutsu.scenario import Relaunch, Scenario


def test_relauncher_relaunches_with_locale_and_overrides() -> None:
    calls: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], env: object = None) -> str:
        calls.append((args, env))
        return ""

    # Scenario locale (ja_JP) overrides the app/config default (en_US from _eff()).
    scn = Scenario.model_validate(
        {"name": "a", "preconditions": {"locale": "ja_JP"}, "steps": [{"tap": {"id": "ok"}}]}
    )
    driver = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])  # 2 elems -> ready immediately
    # extra_env (the device's collector url) must survive the relaunch.
    relaunch = device_relauncher(
        "UDID-1", env_run=fake_run, extra_env={"BAJUTSU_COLLECTOR": "http://127.0.0.1:9"}
    )(_eff(), scn, driver)
    relaunch(Relaunch(env={"K": "V"}, args=["--fresh"]))

    assert any(
        c[0] == ["xcrun", "simctl", "terminate", "UDID-1", "com.example.demo"] for c in calls
    )
    launch, launch_env = next(c for c in calls if "launch" in c[0])
    assert "--fresh" in launch  # per-relaunch arg
    # Locale forced via app launch args, scenario locale winning.
    assert launch[launch.index("-AppleLocale") + 1] == "ja_JP"
    assert "(ja)" in launch
    # The collector url survives the relaunch and the per-relaunch env override is applied
    # (both reach the app via the SIMCTL_CHILD_ child-env channel).
    assert launch_env.get("SIMCTL_CHILD_BAJUTSU_COLLECTOR") == "http://127.0.0.1:9"
    assert launch_env.get("SIMCTL_CHILD_K") == "V"


def _scn(name: str) -> Scenario:
    return Scenario.model_validate({"name": name, "steps": [{"tap": {"id": "ok"}}]})


def test_device_pool_per_device_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool of >1 devices gives each leased scenario its own collector (distinct url),
    interval-recording sink (bound to the udid), and device control — the three features
    that used to drop in parallel."""
    calls: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append((args, extra_env))
        return ""

    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )

    lease, shutdown = device_pool(
        ["UDID-A", "UDID-B"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=fake_run,
    )
    la = lb = None
    try:
        la = lease(_eff(), _scn("a"))
        lb = lease(_eff(), _scn("b"))
        # Distinct collectors on distinct ports (no shared single-loopback receiver).
        assert la.collector is not None and lb.collector is not None
        assert la.collector is not lb.collector
        assert la.collector.port != lb.collector.port
        # Per-device sink bound to the leased udid -> interval evidence works in parallel.
        assert la.sink.udid == "UDID-A" and lb.sink.udid == "UDID-B"
        # Device control present per device, routing to the leased udid.
        assert la.control is not None and lb.control is not None
        la.control.set_location(35.0, 139.0)
        assert any(
            c[0] == ["xcrun", "simctl", "location", "UDID-A", "set", "35.0,139.0"] for c in calls
        )
        # Each app launched pointing at its own device's collector url.
        launch_envs = [e for args, e in calls if "launch" in args]
        urls = {e.get("SIMCTL_CHILD_BAJUTSU_COLLECTOR") for e in launch_envs}
        assert urls == {
            f"http://127.0.0.1:{la.collector.port}",
            f"http://127.0.0.1:{lb.collector.port}",
        }
    finally:
        if la is not None:
            la.release()
        if lb is not None:
            lb.release()
        shutdown()
    # shutdown() stops every device's collector.
    assert la is not None and lb is not None
    assert la.collector._server is None and lb.collector._server is None


def test_device_pool_labels_leased_simulator(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pool reads the simulator catalog once and tags each lease with its device model /
    # OS runtime, so the report's Environment tab can name the simulator a scenario ran on.
    catalog = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-17-2": [
                    {"udid": "UDID-A", "name": "iPhone 15"}
                ],
            }
        }
    )

    def fake_run(args: list[str], extra_env: object = None) -> str:
        return catalog if args == env.list_devices_cmd() else ""

    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["idb"],
        _eff(),
        Path("runs"),
        available=lambda b: True,
        env_run=fake_run,
    )
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.device_name == "iPhone 15" and lz.device_runtime == "iOS 17.2"
    finally:
        shutdown()


def test_device_pool_single_device_keeps_full_features(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pool of one is the single-device path: collector + interval sink + control, all on."""
    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    lease, shutdown = device_pool(
        ["UDID-1"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=True,
        log_subsystem="com.example.demo",
        available=lambda b: True,
        env_run=lambda args, extra_env=None: "",
    )
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.collector is not None  # network collection in a pool of one
        assert lz.sink.udid == "UDID-1"  # interval evidence bound to the device
        assert lz.control is not None  # device control available
        assert lz.relaunch is not None  # relaunch wired to the device
        lz.release()
    finally:
        shutdown()


def test_device_pool_no_network_has_no_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    """--no-network: the pool builds no collectors and injects no collector url."""
    calls: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append((args, extra_env))
        return ""

    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    lease, shutdown = device_pool(
        ["UDID-1"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=False,
        available=lambda b: True,
        env_run=fake_run,
    )
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.collector is None
        launch_envs = [e for args, e in calls if "launch" in args]
        assert all("SIMCTL_CHILD_BAJUTSU_COLLECTOR" not in (e or {}) for e in launch_envs)
        lz.release()
    finally:
        shutdown()


def test_device_pool_stops_started_collectors_when_one_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a collector fails to start mid-setup, the ones already started must be stopped so
    the pool doesn't leak listening sockets."""
    started: list[object] = []

    class FlakyCollector:
        count = 0

        def __init__(self) -> None:
            FlakyCollector.count += 1
            self._idx = FlakyCollector.count
            self.stopped = False

        def start(self) -> None:
            if self._idx == 2:  # the second device's collector fails to bind
                raise OSError("port in use")
            started.append(self)

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("bajutsu.runner.pool.NetworkCollector", FlakyCollector)
    monkeypatch.setattr("bajutsu.environment.make_driver", lambda actuator, udid: FakeDriver([]))

    with pytest.raises(OSError, match="port in use"):
        device_pool(
            ["UDID-A", "UDID-B"],
            ["idb"],
            _eff(),
            Path("runs"),
            network=True,
            available=lambda b: True,
            env_run=lambda args, extra_env=None: "",
        )
    assert len(started) == 1 and started[0].stopped  # type: ignore[attr-defined]


class _StubCollector:
    """A minimal `Collector` for the web lease test (the real one needs a Playwright page)."""

    def __init__(self) -> None:
        self.stopped = False

    def snapshot(self) -> list[NetworkExchange]:
        return []

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return []

    def clear(self) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class _FakeWeb(FakeDriver):
    """A fake web driver: a FakeDriver plus the web-only navigate()/close() lifecycle."""

    def __init__(self, screen: list[base.Element]) -> None:
        super().__init__(screen)
        self.navigated = 0
        self.closed = 0
        self.collector_mocks: object = "unset"
        self.collector: _StubCollector | None = None

    def navigate(self) -> None:
        self.navigated += 1

    def close(self) -> None:
        self.closed += 1

    def network_collector(self, mocks: object = None) -> _StubCollector:
        self.collector_mocks = mocks
        self.collector = _StubCollector()
        return self.collector


def _eff_web() -> Effective:
    return dataclasses.replace(_eff(), base_url="http://x/index.html", backend=["web"])


def test_device_pool_web_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """The web lane: no simctl catalog/control/collector; the driver owns the browser, so
    launch == navigate, relaunch == re-navigate, and release == close."""
    fakes: list[_FakeWeb] = []

    def fake_make_driver(
        actuator: str,
        udid: str,
        base_url: str | None = None,
        headless: bool = True,
        browser: str = "chromium",
        record_video_dir: object = None,
    ) -> base.Driver:
        assert actuator == "playwright"
        assert base_url == "http://x/index.html"  # threaded from eff.base_url
        assert headless is True  # threaded from eff.headless (default headless)
        assert browser == "chromium"  # threaded from eff.browser (default engine, BE-0076)
        d = _FakeWeb([_el("home.title", "H"), _el("ok", "OK")])
        fakes.append(d)
        return d

    monkeypatch.setattr("bajutsu.environment.make_driver", fake_make_driver)
    lease, shutdown = device_pool(
        ["web"], ["web"], _eff_web(), Path("runs"), network=False, available=lambda b: True
    )
    try:
        leased = lease(_eff_web(), _scn("a"))
        assert leased.control is None  # no simctl device control
        assert leased.collector is None  # network off for web
        assert leased.sink.udid == "web"
        assert fakes[0].navigated == 1  # launch == navigate to base_url
        assert leased.relaunch is not None
        leased.relaunch(Relaunch())  # re-navigate, no device restart
        assert fakes[0].navigated == 2
        leased.release()  # tears the browser down
        assert fakes[0].closed == 1
    finally:
        shutdown()


def test_device_pool_web_lease_builds_a_page_hooked_collector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--network` on web: no up-front HTTP receiver; the lease hooks a collector to the live
    page and threads this scenario's mocks into it (BE-0054). The collector satisfies the
    `Collector` protocol and is stopped on release."""
    from bajutsu.network import Collector

    fakes: list[_FakeWeb] = []

    def fake_make_driver(
        actuator: str,
        udid: str,
        base_url: str | None = None,
        headless: bool = True,
        browser: str = "chromium",
        record_video_dir: object = None,
    ) -> base.Driver:
        d = _FakeWeb([_el("home", "H"), _el("ok", "OK")])
        fakes.append(d)
        return d

    monkeypatch.setattr("bajutsu.environment.make_driver", fake_make_driver)
    lease, shutdown = device_pool(
        ["web"], ["web"], _eff_web(), Path("runs"), network=True, available=lambda b: True
    )
    try:
        scn = Scenario.model_validate(
            {"name": "a", "mocks": [{"match": {"path": "/x"}}], "steps": [{"tap": {"id": "ok"}}]}
        )
        leased = lease(_eff_web(), scn)
        assert isinstance(leased.collector, Collector)  # protocol-satisfying
        assert leased.collector is fakes[0].collector  # the page-hooked collector, not an HTTP one
        assert fakes[0].collector_mocks == scn.mocks  # this scenario's mocks were wired in
        leased.release()
        assert fakes[0].collector is not None and fakes[0].collector.stopped is True
    finally:
        shutdown()


def test_device_pool_web_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # A web app with no baseUrl fails cleanly at launch (env.DeviceError), not deep in Playwright.
    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid, base_url=None: FakeDriver([]),
    )
    eff_no_url = dataclasses.replace(_eff(), base_url=None, backend=["web"])
    lease, shutdown = device_pool(
        ["web"], ["web"], eff_no_url, Path("runs"), network=False, available=lambda b: True
    )
    try:
        with pytest.raises(env.DeviceError, match="baseUrl"):
            lease(eff_no_url, _scn("a"))
    finally:
        shutdown()


def test_device_pool_uses_a_resolved_network_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # When a same-platform read-only provider is resolved (BE-0020), its collector supplies network
    # instead of the actuator's app-side one, and the lease's provenance names it as a fallback.
    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    ex = NetworkExchange(method="GET", path="/items", status=200)
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
        make_driver=lambda actuator, udid: FakeDriver(exchanges=[ex]),
        evidence_providers=lambda backends, actuator, available: ({"network": "fake"}, {}),
    )
    lz = None
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.collector_provider == "fake (fallback)"
        assert lz.collector is not None and lz.collector.snapshot() == [ex]
    finally:
        if lz is not None:
            lz.release()
        shutdown()


def test_device_pool_releases_resources_when_launch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # If launch_driver raises after the fallback collector is built (BE-0020), the lease must stop
    # that collector and return the udid to the pool, so one failure neither leaks a socket nor
    # starves later leases. A flaky launch fails once; the retry must then lease the freed device
    # (a never-returned udid would block free.get() forever).
    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )

    class _RecordingCollector:
        def __init__(self) -> None:
            self.stopped = False

        def snapshot(self) -> list[NetworkExchange]:
            return []

        def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
            return []

        def clear(self) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

    built: list[_RecordingCollector] = []

    class _Provider:
        def network_collector(self, mocks: object = None) -> _RecordingCollector:
            c = _RecordingCollector()
            built.append(c)
            return c

    launches = {"n": 0}

    def flaky_launch(*args: object, **kwargs: object) -> base.Driver:
        launches["n"] += 1
        if launches["n"] == 1:
            raise env.DeviceError("boot failed")
        return FakeDriver([_el("home", "H"), _el("ok", "OK")])

    monkeypatch.setattr("bajutsu.runner.pool.launch_driver", flaky_launch)

    lease, shutdown = device_pool(
        ["UDID-A"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
        make_driver=lambda actuator, udid: _Provider(),
        evidence_providers=lambda backends, actuator, available: ({"network": "fake"}, {}),
    )
    lz = None
    try:
        with pytest.raises(env.DeviceError, match="boot failed"):
            lease(_eff(), _scn("a"))
        # The collector built for the failed attempt was stopped (no leaked socket).
        assert len(built) == 1 and built[0].stopped is True
        # The device was returned: a retry leases it (would block forever otherwise).
        lz = lease(_eff(), _scn("a"))
        assert lz.udid == "UDID-A"
    finally:
        if lz is not None:
            lz.release()
        shutdown()


def test_device_pool_network_lease_defaults_to_collector_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no fallback resolved (today's iOS), the app-side collector supplies network and the
    # provenance stays "collector".
    monkeypatch.setattr(
        "bajutsu.environment.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),
    )
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["idb"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
    )
    lz = None
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.collector_provider == "collector"
    finally:
        if lz is not None:
            lz.release()
        shutdown()
