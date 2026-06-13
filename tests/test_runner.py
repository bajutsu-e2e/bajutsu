"""Tests for the run pipeline (config + scenarios + device pool -> report)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bajutsu import env
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import NullSink
from bajutsu.runner import (
    Lease,
    device_pool,
    device_relauncher,
    launch_driver,
    run_all,
    run_and_report,
)
from bajutsu.scenario import Preconditions, Redact, Relaunch, Scenario


def _eff() -> Effective:
    return Effective(
        app="demo",
        bundle_id="com.example.demo",
        deeplink_scheme=None,
        backend=["fake"],
        device="iPhone 15",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=["screenshot.after"],
        redact=Redact(),
    )


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _fake_driver() -> base.Driver:
    return FakeDriver([_el("ok", "OK", ["button"])])  # a screen that always contains "ok"


# A lease over a fake driver with no per-device resources (no evidence/network/control).
def _lease(eff: Effective, scenario: Scenario) -> Lease:
    return Lease(
        driver=_fake_driver(),
        sink=NullSink(),
        relaunch=None,
        control=None,
        collector=None,
        release=lambda: None,
    )


def test_run_all() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "missing"}}]}),
    ]
    results = run_all(_eff(), scenarios, _lease)
    assert [r.ok for r in results] == [True, False]


def test_run_all_parallel_preserves_order_and_releases() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]})
        for n in ("a", "b", "c")
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    assert [r.scenario for r in results] == ["a", "b", "c"]  # order preserved despite concurrency
    assert all(r.ok for r in results)
    assert len(released) == 3 and set(released) == {"a", "b", "c"}  # every leased device released


def test_run_all_releases_after_each_scenario() -> None:
    scenarios = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "ok"}}]}),
    ]
    released: list[str] = []

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: released.append(s.name),
        )

    run_all(_eff(), scenarios, lease)
    assert released == ["a", "b"]  # release runs after every scenario, including the last


def test_run_all_on_blocked_for_selects_per_scenario() -> None:
    # The factory picks each scenario's guard from its dismissAlerts: the guarded scenario
    # recovers from a blocked tap and passes; the one that disabled it fails.
    from bajutsu.orchestrator import AlertEvent, BlockedHandler

    scenarios = [
        Scenario.model_validate(
            {"name": "guarded", "dismissAlerts": True, "steps": [{"tap": {"id": "later"}}]}
        ),
        Scenario.model_validate(
            {"name": "bare", "dismissAlerts": False, "steps": [{"tap": {"id": "later"}}]}
        ),
    ]

    def recover(d: base.Driver) -> AlertEvent:
        assert isinstance(d, FakeDriver)
        d.screen = [_el("later", "Later", ["button"])]  # "dismiss the alert": target appears
        return AlertEvent(label="x")

    def on_blocked_for(s: Scenario) -> BlockedHandler | None:
        cfg = s.dismiss_alerts
        return None if cfg is not None and not cfg.enabled else recover

    results = run_all(_eff(), scenarios, _lease, on_blocked_for=on_blocked_for)
    assert [r.ok for r in results] == [True, False]


def test_run_all_attributes_each_scenario_to_its_device() -> None:
    scenarios = [
        Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in ("a", "b")
    ]

    def lease(eff: Effective, s: Scenario) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            udid=f"DEV-{s.name}",
        )

    results = run_all(_eff(), scenarios, lease, workers=2)
    # Each result records the device that ran it, so the report can show the parallel split.
    assert {r.scenario: r.device for r in results} == {"a": "DEV-a", "b": "DEV-b"}


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


def test_launch_driver_shuts_down_before_erase(monkeypatch: pytest.MonkeyPatch) -> None:
    """erase requires a shut-down device, so the sequence is shutdown -> erase -> boot."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    ready = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])  # 2 elems -> ready immediately
    monkeypatch.setattr("bajutsu.runner.make_driver", lambda actuator, udid: ready)

    launch_driver("UDID-1", _eff(), "idb", Preconditions(erase=True), env_run=fake_run)

    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert verbs.index("shutdown") < verbs.index("erase") < verbs.index("boot")
    assert verbs.index("boot") < verbs.index("launch")  # boot before launching the app


def test_launch_driver_injects_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_env (e.g. the device's collector url) reaches the app via the launch child env."""
    calls: list[tuple[list[str], object]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append((args, extra_env))
        return ""

    ready = FakeDriver([_el("home.title", "H"), _el("ok", "OK")])
    monkeypatch.setattr("bajutsu.runner.make_driver", lambda actuator, udid: ready)

    launch_driver(
        "UDID-1",
        _eff(),
        "idb",
        Preconditions(erase=False),
        env_run=fake_run,
        extra_env={"BAJUTSU_COLLECTOR": "http://127.0.0.1:7"},
    )

    _, launch_env = next(c for c in calls if "launch" in c[0])
    assert launch_env.get("SIMCTL_CHILD_BAJUTSU_COLLECTOR") == "http://127.0.0.1:7"


def _recording_run(calls: list[list[str]]):
    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return ""

    return fake_run


def _launch_recording(
    monkeypatch: pytest.MonkeyPatch, app_path: str, pre: Preconditions
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "bajutsu.runner.make_driver",
        lambda actuator, udid: FakeDriver([_el("home.title", "H"), _el("ok", "OK")]),
    )
    launch_driver(
        "UDID-1", replace(_eff(), app_path=app_path), "idb", pre, env_run=_recording_run(calls)
    )
    return calls


def test_launch_driver_reinstall_clean_uninstalls_then_installs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default `reinstall: clean` removes the app then installs it fresh before each run."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(monkeypatch, str(app), Preconditions(erase=False))  # reinstall=clean
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "uninstall" in verbs and "install" in verbs
    assert verbs.index("uninstall") < verbs.index("install")  # remove, then install fresh
    assert ["xcrun", "simctl", "install", "UDID-1", str(app)] in calls


def test_launch_driver_reinstall_overwrite_installs_without_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`reinstall: overwrite` installs over the existing app (no uninstall, keeps data)."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(
        monkeypatch, str(app), Preconditions(erase=False, reinstall="overwrite")
    )
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "install" in verbs and "uninstall" not in verbs


def test_launch_driver_erase_skips_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An `erase` already wiped the app, so `clean` skips the redundant uninstall and installs."""
    app = tmp_path / "X.app"
    app.mkdir()
    calls = _launch_recording(monkeypatch, str(app), Preconditions(erase=True))  # reinstall=clean
    verbs = [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]
    assert "erase" in verbs and "install" in verbs and "uninstall" not in verbs


def test_launch_driver_errors_on_missing_app_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured appPath that doesn't exist fails with a clear, actionable DeviceError."""
    eff = replace(_eff(), app_path="/nope/X.app")
    monkeypatch.setattr("bajutsu.runner.make_driver", lambda actuator, udid: FakeDriver([]))
    with pytest.raises(env.DeviceError) as excinfo:
        launch_driver("UDID-1", eff, "idb", Preconditions(erase=False), env_run=_recording_run([]))
    assert "appPath not found" in str(excinfo.value)


def test_launch_driver_surfaces_failing_erase_as_device_error() -> None:
    """A simctl failure becomes a clean DeviceError (exit 2 at the CLI), not a traceback."""

    def fake_run(args: list[str], extra_env: object = None) -> str:
        if args[:3] == ["xcrun", "simctl", "erase"]:
            raise subprocess.CalledProcessError(
                149,
                args,
                output="",
                stderr="Unable to erase contents and settings in current state: Booted",
            )
        return ""

    with pytest.raises(env.DeviceError) as excinfo:
        launch_driver("UDID-1", _eff(), "idb", Preconditions(erase=True), env_run=fake_run)
    msg = str(excinfo.value)
    assert "exit 149" in msg
    assert "Booted" in msg  # simctl's actionable stderr is carried through


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
        "bajutsu.runner.make_driver",
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
        "bajutsu.runner.make_driver",
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
        "bajutsu.runner.make_driver",
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
        "bajutsu.runner.make_driver",
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

    monkeypatch.setattr("bajutsu.runner.NetworkCollector", FlakyCollector)
    monkeypatch.setattr("bajutsu.runner.make_driver", lambda actuator, udid: FakeDriver([]))

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


def test_run_and_report(tmp_path: Path) -> None:
    scenarios = [Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "ok"}}]})]
    results, manifest = run_and_report(_eff(), scenarios, _lease, tmp_path / "runs", "run1")
    assert results[0].ok
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["runId"] == "run1"
    assert (tmp_path / "runs" / "run1" / "junit.xml").exists()
    # The executed scenario is kept alongside its results.
    scn_file = tmp_path / "runs" / "run1" / "scenario.yaml"
    assert scn_file.exists() and "name: a" in scn_file.read_text(encoding="utf-8")


def test_run_and_report_scrubs_secret_values_from_artifacts(tmp_path: Path) -> None:
    """The run-level scrub is the final safety net: a secret that reaches result text (here a
    failing assertion's expected value, interpolated from a binding) must not survive into any
    written artifact, even though the scenario definition only ever holds the token."""
    secret = "S3CR3T-TOKEN"
    scenarios = [
        Scenario.model_validate(
            {
                "name": "a",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"value": {"sel": {"id": "ok"}, "equals": "${secrets.token}"}}],
            }
        )
    ]
    results, _ = run_and_report(
        _eff(),
        scenarios,
        _lease,
        tmp_path / "runs",
        "run1",
        bindings={"secrets.token": secret},
        secret_values=[secret],
    )
    # The assertion failed, so the secret value really did reach the in-memory result text.
    assert not results[0].ok
    assert results[0].failure is not None and secret in results[0].failure
    # ...but it is scrubbed out of every written artifact.
    run_dir = tmp_path / "runs" / "run1"
    for name in ("manifest.json", "junit.xml", "scenario.yaml"):
        assert secret not in (run_dir / name).read_text(encoding="utf-8")
