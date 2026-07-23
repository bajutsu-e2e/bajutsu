"""Tests for the XCUITest environment's target selection (BE-0238 Unit 1).

BE-0019 drove only the Simulator; BE-0238 generalises the same `xcodebuild test-without-building`
driving layer to a real iOS device, where the only difference is the `-destination` platform and
that the simctl device-prep (erase / boot / install / permissions) does not apply. The Simulator's
own boot path still needs Xcode, so it stays off the gate; here the target-selection logic and the
loud refusal of simctl-only operations against a real device are exercised without one — the
`xcodebuild`/toolchain boundary is the sanctioned fake point (the same one BE-0019 fakes).
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from bajutsu import backends, simctl
from bajutsu.config import Effective, load_config, resolve
from bajutsu.drivers.xcuitest import XcuitestChannelError
from bajutsu.platform_lifecycle.environments.xcuitest import (
    _WARM_HEALTH_TIMEOUT,
    XcuitestEnvironment,
    _destination,
)
from bajutsu.scenario import Preconditions

_DEVICE_UDID = "00008030-000A1B2C3D4E"  # a physical-device id shape (not a simctl UUID)


def _device_eff(*, app_path: str | None = None, test_runner: str | None = None) -> Effective:
    lines = ["targets:", "  s:", "    bundleId: com.x"]
    if app_path is not None:
        lines.append(f"    appPath: {app_path}")
    lines += ["    xcuitest:", "      deviceType: device"]
    if test_runner is not None:
        lines.append(f"      testRunner: {test_runner}")
    return resolve(load_config("\n".join(lines) + "\n"), "s")


# --- the destination string (pure) --- #


def test_destination_targets_the_simulator_by_default() -> None:
    assert _destination("simulator", "ABC123") == "platform=iOS Simulator,id=ABC123"


def test_destination_targets_a_real_device() -> None:
    # A real device drops the "Simulator" suffix; xcodebuild then addresses the attached device.
    assert _destination("device", _DEVICE_UDID) == f"platform=iOS,id={_DEVICE_UDID}"


def test_destination_validates_the_udid() -> None:
    # A leading-dash id would be read by xcodebuild as an option — refuse it (the shared device_id
    # policy `validated_udid` enforces, applied to a real-device id the same as a simulator one).
    with pytest.raises(simctl.DeviceError):
        _destination("device", "-rf")


# --- real-device start(): skip simctl, refuse simctl-only operations loudly --- #


def test_start_on_a_real_device_refuses_simctl_install() -> None:
    # A real device installs its build out of band; asking the simctl installer to place `appPath`
    # must fail loudly (BE-0238 Unit 2/3), never silently skip (determinism first).
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(app_path="build/App.app"), Preconditions())


def test_start_on_a_real_device_refuses_permission_grants() -> None:
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(), Preconditions(), permissions={"camera": "yes"})


def test_start_on_a_real_device_refuses_erase() -> None:
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID)
    with pytest.raises(simctl.DeviceError, match="real device"):
        env.start(_device_eff(), Preconditions(erase=True))


def test_start_on_a_real_device_targets_the_device_and_skips_simctl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The happy real-device path: no simctl call, and the xcodebuild destination is the device.
    # Faked at the process boundary (Popen) and the driver factory — the runner needs Xcode + a
    # device, which the gate has neither of.
    runner = tmp_path / "Runner.xctestrun"
    with runner.open("wb") as f:
        plistlib.dump({"Target": {"TestingEnvironmentVariables": {}}}, f)

    simctl_calls: list[list[str]] = []

    def _record_run(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        simctl_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    captured: dict[str, list[str]] = {}

    class _FakeProc:
        def terminate(self) -> None: ...
        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _fake_popen(argv: list[str], **_kw: Any) -> _FakeProc:
        captured["argv"] = argv
        return _FakeProc()

    class _FakeDriver:
        def await_ready(self, timeout: float) -> None: ...

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _FakeDriver())

    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID, env_run=_record_run)
    env.start(_device_eff(test_runner=str(runner)), Preconditions())

    assert f"platform=iOS,id={_DEVICE_UDID}" in captured["argv"]
    assert simctl_calls == []  # a real device is never touched through simctl
    # A real-device runner is torn down per lease, never kept warm: this is the one guard keeping the
    # pool's warm cache (runner/pool.py) from reusing a real device's runner across scenarios, so an
    # inverted condition here would silently skip its per-lease teardown (BE-0291).
    assert not env.has_reusable_resident()


# --- the live-route boundary: an Appium endpoint routes around the udid machinery (BE-0238) --- #


def test_destination_still_rejects_a_url_as_a_udid() -> None:
    # `_destination` itself is unchanged: a URL passed to it directly is still rejected by the shared
    # device_id policy (its `//` is outside the charset). This is the defense-in-depth guard — the live
    # route now keeps a real endpoint away from `_destination` entirely (see the routing test below),
    # so this only fires if a URL ever reached the simctl / xcodebuild udid machinery by mistake.
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        _destination("device", "http://grid.local:4723")


def test_appium_lease_endpoint_routes_to_the_live_environment() -> None:
    # The live transport (Slice A) closes the Unit 4 boundary: the endpoint the `appium` provider
    # yields no longer flows into `_destination`. `environment_for` recognises the `http(s)://` udid
    # spec and returns the live WebDriver environment, which drives the reserved device off the simctl
    # / xcodebuild path — so the endpoint reaches the WebDriver session, never the udid machinery.
    from bajutsu.platform_lifecycle.environments.xcuitest_live import XcuitestLiveEnvironment
    from bajutsu.platform_lifecycle.factories import environment_for
    from bajutsu.runner import device_provider as dp

    cfg = load_config(
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      deviceType: device\n"
        "    deviceProvider:\n      kind: appium\n      endpoint: http://grid.local:4723\n"
    )
    lease = dp.acquire_device(resolve(cfg, "s"), "booted")
    assert lease.udid_spec == "http://grid.local:4723"  # the endpoint, not the --udid flag
    env = environment_for("xcuitest", lease.udid_spec)
    assert isinstance(env, XcuitestLiveEnvironment)
    # The live environment passes the endpoint through instead of resolving it through simctl.
    assert env.resolve_device(lease.udid_spec) == "http://grid.local:4723"


# --- warm runner reuse across leases on a Simulator (BE-0291) --- #
#
# The Simulator's own boot needs Xcode, so it stays off the gate; the `xcodebuild`/toolchain boundary
# (Popen), the driver factory, and simctl are the sanctioned fake points (as in the BE-0019 tests
# above). These exercise the reuse *logic* — spawn once, resume, respawn on erase / a wedged runner —
# without a Simulator.


def _sim_eff(*, test_runner: str) -> Effective:
    cfg = f"targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      testRunner: {test_runner}\n"
    return resolve(load_config(cfg), "s")


class _FakeProc:
    """A fake runner process: `poll()` reports liveness, `terminate()`/`kill()` end it."""

    def __init__(self) -> None:
        self.alive = True
        self.terminated = (
            False  # observed by the mid-run-crash test (a dead runner is not signalled)
        )

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.alive = False


def _write_runner(tmp_path: Path) -> Path:
    runner = tmp_path / "Runner.xctestrun"
    with runner.open("wb") as f:
        plistlib.dump({"Target": {"TestingEnvironmentVariables": {}}}, f)
    return runner


def _fake_toolchain(
    monkeypatch: pytest.MonkeyPatch, *, wedged: dict[str, bool] | None = None
) -> tuple[list[list[str]], list[list[str]], simctl.RunFn]:
    """Fake Popen (the runner), the driver factory, and simctl; return (popen log, simctl log, run).

    The returned `run` is the fake simctl runner to hand the environment as `env_run`. `wedged`, when
    given, makes the driver's *warm* health probe (`_WARM_HEALTH_TIMEOUT`) raise while `wedged["v"]`
    is True, so a test can wedge the reused runner; the cold-startup `await_ready` (the long timeout)
    always succeeds, so a respawn still comes up.
    """
    popen_argvs: list[list[str]] = []
    simctl_calls: list[list[str]] = []

    def _popen(argv: list[str], **_kw: Any) -> _FakeProc:
        popen_argvs.append(argv)
        return _FakeProc()

    class _Driver:
        def await_ready(self, timeout: float = 10.0) -> None:
            if wedged is not None and wedged["v"] and timeout == _WARM_HEALTH_TIMEOUT:
                raise XcuitestChannelError("wedged")

    def _run(argv: list[str], env: object = None) -> subprocess.CompletedProcess[bytes]:
        simctl_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _Driver())
    return popen_argvs, simctl_calls, _run


def test_start_reuses_a_healthy_runner_across_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The core amortization: a second lease on the same device resumes the live runner (app relaunch
    # only) instead of spawning a second `xcodebuild` — the runner's cold startup is paid once.
    popen_argvs, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    env.start(eff, Preconditions())  # cold: spawn the runner
    assert env.has_reusable_resident()
    env.start(eff, Preconditions())  # warm: reuse it
    assert len(popen_argvs) == 1  # the runner was spawned once and reused (BE-0291)


def test_start_respawns_the_runner_when_the_scenario_erases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `erase` shuts the Simulator down (killing the runner), so it cannot reuse a warm one — the
    # scenario respawns cold. The reset still runs before the app launches, keeping isolation (Unit 2).
    popen_argvs, simctl_calls, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    env.start(eff, Preconditions())
    env.start(eff, Preconditions(erase=True))
    assert len(popen_argvs) == 2  # the erase forced a fresh runner
    assert any(c[:3] == ["xcrun", "simctl", "erase"] for c in simctl_calls)  # the device was erased


def test_start_respawns_a_wedged_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # BE-0291 Unit 4: a warm runner that fails its bounded /health probe (the known crash after
    # repeated app.launch() cycles) is a cache miss — the next lease respawns cold rather than losing
    # the run. One scenario's fault costs one extra cold start.
    wedged = {"v": False}
    popen_argvs, _, run = _fake_toolchain(monkeypatch, wedged=wedged)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    env.start(eff, Preconditions())
    wedged["v"] = True  # the runner wedges after the first lease
    env.start(eff, Preconditions())
    assert len(popen_argvs) == 2  # the wedged runner was discarded and a fresh one spawned


def test_end_lease_keeps_the_runner_but_teardown_terminates_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `end_lease` releases a lease while keeping the warm runner (only the app is terminated); the
    # pool's later `teardown` is what actually kills it (BE-0291 ownership on the pool).
    _, simctl_calls, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    driver = env.start(eff, Preconditions())
    proc = env._runner_proc

    simctl_calls.clear()
    env.end_lease(driver, eff)
    assert env._runner_proc is proc and proc is not None and proc.alive  # runner untouched
    assert env.has_reusable_resident()
    assert any(
        c[:3] == ["xcrun", "simctl", "terminate"] for c in simctl_calls
    )  # only the app ended

    env.teardown(driver, eff)
    assert env._runner_proc is None and not proc.alive  # teardown kills the runner
    assert not env.has_reusable_resident()


def test_start_respawns_a_dead_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # BE-0291 Unit 4: a runner whose process has exited (crashed) between leases is discarded before
    # any /health probe (the `poll()` fast path) — the next lease respawns cold.
    popen_argvs, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    env.start(eff, Preconditions())
    assert env._runner_proc is not None
    env._runner_proc.alive = False  # type: ignore[attr-defined]  # the runner process exited
    env.start(eff, Preconditions())
    assert len(popen_argvs) == 2  # the dead runner was discarded and a fresh one spawned


def test_discarding_a_crashed_runner_warns_and_does_not_signal_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The diagnostic seam: a runner that exited on its own (the known app.launch()-cycle crash) is
    # discarded without a terminate() — the pid is already reaped — and logs a mid-run-crash warning
    # so a run that died on a `Connection refused` shows *why* the channel went away.
    _, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    proc = env._runner_proc
    assert proc is not None
    proc.alive = False  # type: ignore[attr-defined]  # the runner crashed mid-run
    with caplog.at_level("WARNING"):
        env._discard_runner()
    assert not proc.terminated  # type: ignore[attr-defined]  # a dead process is never signalled
    assert "exited on its own" in caplog.text


def test_runner_output_is_captured_when_the_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BAJUTSU_XCUITEST_RUNNER_LOG opts into capturing the runner's stdout/stderr to a file, so the
    # crash is diagnosable; the crash warning then points at that file rather than telling the user
    # how to enable it. Off (the default), no file is opened and `_runner_log` stays None.
    _, _, run = _fake_toolchain(monkeypatch)
    log_dir = tmp_path / "runner-logs"
    monkeypatch.setenv("BAJUTSU_XCUITEST_RUNNER_LOG", str(log_dir))
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    assert env._runner_log is not None and env._runner_log.parent == log_dir
    assert env._runner_log.exists()  # the sink file was opened for the spawn
    assert (
        "see" in env._runner_log_hint()
    )  # the hint points at the captured log, not at the env var


def test_warm_resume_reapplies_the_per_scenario_reset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0291 Unit 2: a reused runner still gets the full per-scenario reset before the app relaunch —
    # reinstall, permission grants, terminate + relaunch, and the deeplink — so reuse never weakens
    # the isolation a cold lease gives. Asserting spawn count alone would miss a skipped reset.
    popen_argvs, simctl_calls, run = _fake_toolchain(monkeypatch)
    app = tmp_path / "App.app"
    app.mkdir()
    cfg = (
        f"targets:\n  s:\n    bundleId: com.x\n    appPath: {app}\n"
        f"    xcuitest:\n      testRunner: {_write_runner(tmp_path)}\n"
    )
    eff = resolve(load_config(cfg), "s")
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(eff, Preconditions())  # cold spawn
    simctl_calls.clear()
    env.start(
        eff, Preconditions(deeplink="myapp://open"), permissions={"camera": "grant"}
    )  # warm resume
    assert len(popen_argvs) == 1  # no respawn — the runner was reused
    verbs = [c[2] for c in simctl_calls if len(c) >= 3 and c[:2] == ["xcrun", "simctl"]]
    # the per-scenario reset ran on the warm path, before the app relaunch:
    assert "install" in verbs  # app reinstalled (reinstall=clean → uninstall + install)
    assert "privacy" in verbs  # the camera permission was granted via `simctl privacy`
    assert "terminate" in verbs and "launch" in verbs  # the app was restarted
    assert "openurl" in verbs  # the deeplink was opened
