"""Tests for the device pool and the per-device relauncher."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _runner import _eff, _el, _web_eff

from bajutsu import simctl
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.network import NetworkExchange
from bajutsu.runner import (
    ReadinessResult,
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
        "bajutsu.backends.make_driver",
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


def test_device_pool_wires_readiness_and_provenance_into_the_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lease folds the launch readiness outcome and this scenario's BE-0049 provenance into the
    sink, so a first-wait timeout diagnostic can state them (BE-0231 Unit 1)."""
    monkeypatch.setattr(
        "bajutsu.backends.make_driver",
        lambda actuator, udid: FakeDriver([_el("home", "H"), _el("ok", "OK")]),  # 2 → count signal
    )
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["idb"],
        _eff(),
        Path("runs"),
        available=lambda b: True,
        env_run=lambda args, extra_env=None: "",
    )
    lz = None
    try:
        lz = lease(_eff(), _scn("a"))
        assert lz.sink.readiness is not None
        assert lz.sink.readiness.signal == "count"
        assert lz.sink.provenance is not None
        assert lz.sink.provenance["scenarioHash"].startswith("sha256:")
        assert "toolVersion" in lz.sink.provenance
    finally:
        if lz is not None:
            lz.release()
        shutdown()


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
        return catalog if args == simctl.list_devices_cmd() else ""

    monkeypatch.setattr(
        "bajutsu.backends.make_driver",
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
        "bajutsu.backends.make_driver",
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
        "bajutsu.backends.make_driver",
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
    monkeypatch.setattr("bajutsu.backends.make_driver", lambda actuator, udid: FakeDriver([]))

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


class _RecordingEnv:
    """A fake RunEnvironment that records the actuator it was built for and whether it was started
    and torn down — enough of the lease seam to prove per-scenario selection and per-lease teardown.
    """

    def __init__(self, actuator: str, udid: str, *, fail_start: bool = False) -> None:
        self.actuator = actuator
        self.udid = udid
        self.started = False
        self.torn = False
        self.fail_start = fail_start
        # BE-0283 bridge recording: the port bridged, whether it was already bridged when start ran
        # (i.e. before launch), and whether its teardown thunk fired.
        self.bridged_port: int | None = None
        self.bridged_before_launch = False
        self.bridge_torn = False

    def start(self, eff: Effective, pre: object, **_: object) -> base.Driver:
        self.bridged_before_launch = self.bridged_port is not None
        if self.fail_start:
            raise RuntimeError("launch failed")
        self.started = True
        return FakeDriver([_el("home", "H"), _el("ok", "OK")])  # 2 elems -> ready on count

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return {}

    def observes_network_via_driver(self) -> bool:
        return False

    def bridge_collector(self, port: int) -> Callable[[], None]:
        self.bridged_port = port

        def remove() -> None:
            self.bridge_torn = True

        return remove

    def records_video_up_front(self) -> bool:
        return False

    def relauncher(
        self, eff: Effective, scenario: Scenario, driver: base.Driver, **_: object
    ) -> Callable[[object], None]:
        return lambda opts: None

    def controller(self, eff: Effective) -> None:
        return None

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        self.torn = True


def test_device_pool_resolves_actuator_per_scenario_and_tears_down_its_own_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-0240: each scenario leases the cheapest actuator its own steps can run on (idb for a plain
    tap, xcuitest for a pinch), and the environment that *starts* a lease is the one that tears it
    down — so a stateful backend's resident runner is terminated by the instance that spawned it."""
    created: list[_RecordingEnv] = []

    def fake_env_for(actuator: str, udid: str, env_run: object = None) -> _RecordingEnv:
        env = _RecordingEnv(actuator, udid)
        created.append(env)
        return env

    monkeypatch.setattr("bajutsu.runner.pool.environment_for", fake_env_for)

    pinch = Scenario.model_validate(
        {"name": "p", "steps": [{"pinch": {"sel": {"id": "m"}, "scale": 2.0}}]}
    )
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["ios"],
        _eff(),
        Path("runs"),
        network=False,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
    )
    try:
        tap_lease = lease(_eff(), _scn("tap"))
        idb_env = created[-1]  # the lease env, not the pool env
        assert idb_env.actuator == "idb" and idb_env.started  # cheapest sufficient for a plain tap
        tap_lease.release()
        assert idb_env.torn  # the SAME instance that started tears down (BE-0240)

        pinch_lease = lease(_eff(), pinch)
        xc_env = created[-1]
        assert xc_env.actuator == "xcuitest" and xc_env.started  # escalated: pinch needs multiTouch
        pinch_lease.release()
        assert xc_env.torn
    finally:
        shutdown()


def test_device_pool_bridges_the_collector_before_launch_and_tears_it_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-0283: for an external-receiver backend (Android), the pool makes the host collector
    reachable from the device before launch (`adb reverse`) and releases the tunnel with the lease."""
    created: list[_RecordingEnv] = []

    def fake_env_for(actuator: str, udid: str, env_run: object = None) -> _RecordingEnv:
        env = _RecordingEnv(actuator, udid)
        created.append(env)
        return env

    monkeypatch.setattr("bajutsu.runner.pool.environment_for", fake_env_for)
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["android"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
    )
    try:
        leased = lease(_eff(), _scn("a"))
        env = created[-1]  # the lease env
        assert env.bridged_port == leased.collector.port  # tunnels the pre-started collector's port
        assert env.bridged_before_launch  # established BEFORE the app launched
        assert not env.bridge_torn
        leased.release()
        assert env.bridge_torn  # the tunnel is released with the lease
    finally:
        shutdown()


def test_device_pool_releases_the_bridge_when_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A launch failure after the bridge is up must not leak the tunnel (BE-0283)."""
    created: list[_RecordingEnv] = []

    def fake_env_for(actuator: str, udid: str, env_run: object = None) -> _RecordingEnv:
        env = _RecordingEnv(actuator, udid, fail_start=True)
        created.append(env)
        return env

    monkeypatch.setattr("bajutsu.runner.pool.environment_for", fake_env_for)
    lease, shutdown = device_pool(
        ["UDID-A"],
        ["android"],
        _eff(),
        Path("runs"),
        network=True,
        available=lambda b: True,
        env_run=lambda *a, **k: "",
    )
    try:
        with pytest.raises(RuntimeError, match="launch failed"):
            lease(_eff(), _scn("a"))
        env = created[-1]
        assert env.bridged_port is not None  # the bridge was established...
        assert env.bridge_torn  # ...and torn down on the failure path
    finally:
        shutdown()


def _eff_web() -> Effective:
    return _web_eff(base_url="http://x/index.html")


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
        device_mode: str = "desktop",
        record_video_dir: object = None,
    ) -> base.Driver:
        assert actuator == "playwright"
        assert base_url == "http://x/index.html"  # threaded from eff.base_url
        assert headless is True  # threaded from eff.headless (default headless)
        assert browser == "chromium"  # threaded from eff.browser (default engine, BE-0076)
        assert device_mode == "desktop"  # threaded from eff.device_mode (default, BE-0228)
        d = _FakeWeb([_el("home.title", "H"), _el("ok", "OK")])
        fakes.append(d)
        return d

    monkeypatch.setattr("bajutsu.backends.make_driver", fake_make_driver)
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
    from bajutsu.evidence.network import Collector

    fakes: list[_FakeWeb] = []

    def fake_make_driver(
        actuator: str,
        udid: str,
        base_url: str | None = None,
        headless: bool = True,
        browser: str = "chromium",
        device_mode: str = "desktop",
        record_video_dir: object = None,
    ) -> base.Driver:
        d = _FakeWeb([_el("home", "H"), _el("ok", "OK")])
        fakes.append(d)
        return d

    monkeypatch.setattr("bajutsu.backends.make_driver", fake_make_driver)
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
    # A web app with no baseUrl fails cleanly at launch (simctl.DeviceError), not deep in Playwright.
    monkeypatch.setattr(
        "bajutsu.backends.make_driver",
        lambda actuator, udid, base_url=None: FakeDriver([]),
    )
    eff_no_url = _web_eff(base_url=None)
    lease, shutdown = device_pool(
        ["web"], ["web"], eff_no_url, Path("runs"), network=False, available=lambda b: True
    )
    try:
        with pytest.raises(simctl.DeviceError, match="baseUrl"):
            lease(eff_no_url, _scn("a"))
    finally:
        shutdown()


def test_device_pool_uses_a_resolved_network_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # When a same-platform read-only provider is resolved (BE-0020), its collector supplies network
    # instead of the actuator's app-side one, and the lease's provenance names it as a fallback.
    monkeypatch.setattr(
        "bajutsu.backends.make_driver",
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
        "bajutsu.backends.make_driver",
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

    def flaky_launch(*args: object, **kwargs: object) -> tuple[base.Driver, ReadinessResult]:
        launches["n"] += 1
        if launches["n"] == 1:
            raise simctl.DeviceError("boot failed")
        return FakeDriver([_el("home", "H"), _el("ok", "OK")]), ReadinessResult(True, "count", 0.0)

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
        with pytest.raises(simctl.DeviceError, match="boot failed"):
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
        "bajutsu.backends.make_driver",
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
