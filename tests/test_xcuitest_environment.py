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
    _MAX_WARM_REUSES,
    _MAX_WARM_REUSES_ENV,
    _WARM_HEALTH_TIMEOUT,
    XcuitestEnvironment,
    _await_cold_runner,
    _destination,
    _spawn_cold_with_retry,
    _Spawned,
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
        def poll(self) -> int | None:
            return None  # alive: the cold-spawn liveness check (BE-0319) never trips

        def terminate(self) -> None: ...
        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _fake_popen(argv: list[str], **_kw: Any) -> _FakeProc:
        captured["argv"] = argv
        return _FakeProc()

    class _FakeDriver:
        def await_ready(self, timeout: float) -> None: ...
        def health_ready(self) -> bool:
            return True  # the cold runner answers /health at once (BE-0319)

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

        def health_ready(self) -> bool:
            return True  # the cold runner answers /health at once (BE-0319 unit 3)

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


def test_start_respawns_cold_before_the_app_launch_cycle_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0287: the resident runner crashes after a handful of app.launch() cycles
    # (docs/architecture.md). The BE-0291 warm probe only detects an *already*-crashed runner, so the
    # crash still lands mid-scenario. Bounding the reuse count respawns the runner cold *before* the
    # crash threshold: after `_MAX_WARM_REUSES` warm reuses (the runner stays healthy throughout), the
    # next lease spawns a fresh runner rather than tipping the live one over.
    popen_argvs, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    for _ in range(_MAX_WARM_REUSES + 1):  # cold spawn, then `_MAX_WARM_REUSES` warm reuses
        env.start(eff, Preconditions())
    assert len(popen_argvs) == 1  # one runner across the whole reuse budget (all warm, all healthy)
    env.start(eff, Preconditions())  # budget spent → proactive cold respawn, not another reuse
    assert len(popen_argvs) == 2
    assert env._warm_reuses == 0  # the fresh runner's cycle count started over


def test_max_warm_reuses_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A lane can retune the reuse budget without a code change; 0 disables warm reuse entirely (every
    # lease is cold), the safe fallback if a runner proves to crash sooner than the default tolerates.
    monkeypatch.setenv(_MAX_WARM_REUSES_ENV, "0")
    popen_argvs, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    env.start(eff, Preconditions())
    env.start(eff, Preconditions())
    assert len(popen_argvs) == 2  # no warm reuse: each lease spawns cold


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
    # BAJUTSU_XCUITEST_RUNNER_LOG overrides the capture directory (capture is on by default since
    # BE-0319); the crash warning then points at the file under that directory, and — unlike a
    # default capture — it is kept, not pruned (see the ephemeral / kept teardown tests below).
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


def test_runner_output_is_captured_by_default_and_is_ephemeral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0319 unit 1: capture is on by default (env unset), so the first CI flake is diagnosable
    # without a human pre-arming BAJUTSU_XCUITEST_RUNNER_LOG. The default capture is ephemeral — a
    # healthy discard prunes it — while a run leaves nothing behind, matching "teardown prunes".
    _, _, run = _fake_toolchain(monkeypatch)
    monkeypatch.delenv("BAJUTSU_XCUITEST_RUNNER_LOG", raising=False)
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._DEFAULT_RUNNER_LOG_DIR",
        tmp_path / "default-logs",
    )
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    driver = env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    log = env._runner_log
    assert log is not None and log.exists()  # captured even with the env var unset
    assert env._runner_log_ephemeral  # a default capture is marked for pruning
    env.teardown(driver, _sim_eff(test_runner=str(_write_runner(tmp_path))))
    assert not log.exists()  # teardown pruned the ephemeral default capture


def test_an_explicit_capture_directory_is_kept_on_teardown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The env-var override is the operator asking to keep the capture: it is not ephemeral, so
    # teardown leaves it in place (unlike the default capture above).
    _, _, run = _fake_toolchain(monkeypatch)
    log_dir = tmp_path / "kept-logs"
    monkeypatch.setenv("BAJUTSU_XCUITEST_RUNNER_LOG", str(log_dir))
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    driver = env.start(eff, Preconditions())
    log = env._runner_log
    assert log is not None and log.parent == log_dir and not env._runner_log_ephemeral
    env.teardown(driver, eff)
    assert log.exists()  # an operator-chosen directory is kept, never pruned


def test_runner_log_hint_shows_the_bounded_tail_of_the_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # BE-0319 unit 2's actual tail extraction: the hint shows the last _RUNNER_LOG_TAIL_LINES of the
    # (high-volume) capture plus the file path — enough to show *why* the runner never answered
    # without dumping the whole log. The loud startup error folds this same hint in.
    _, _, run = _fake_toolchain(monkeypatch)
    monkeypatch.setenv("BAJUTSU_XCUITEST_RUNNER_LOG", str(tmp_path / "logs"))
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    assert env._runner_log is not None
    env._runner_log.write_text("".join(f"line-{i}\n" for i in range(50)))
    hint = env._runner_log_hint()
    assert f"see {env._runner_log}" in hint  # the path is named
    assert "line-49" in hint and "line-30" in hint  # the last 20 lines are shown
    assert "line-29" not in hint and "line-00" not in hint  # earlier lines are dropped


def test_a_repeatable_cold_spawn_failure_fails_loudly_and_keeps_the_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # End-to-end over the real _spawn_cold: a runner whose `xcodebuild` exits at once (health never
    # ready, process dead) fails fast on both attempts and raises loudly — no 120s dead-wait, no
    # misleading "mid-run crash" warning (its reason is in the error), and both attempts' captured
    # logs are kept on disk as evidence past the 20-line tail (BE-0319 units 1/3/4).
    module = "bajutsu.platform_lifecycle.environments.xcuitest"
    monkeypatch.setattr(f"{module}._RUNNER_STARTUP_TIMEOUT", 0.05)
    monkeypatch.setattr(f"{module}._DEFAULT_RUNNER_LOG_DIR", tmp_path / "logs")
    monkeypatch.delenv("BAJUTSU_XCUITEST_RUNNER_LOG", raising=False)

    class _DeadProc:
        def poll(self) -> int:
            return 71  # the xcodebuild process exited immediately — never bound its port

        def terminate(self) -> None: ...
        def wait(self, timeout: float | None = None) -> int:
            return 71

        def kill(self) -> None: ...

    class _Driver:
        def health_ready(self) -> bool:
            return False  # /health never answers ready

        def await_ready(self, timeout: float = 10.0) -> None: ...

    def _run(argv: list[str], env: object = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _DeadProc())
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _Driver())
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=_run)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))
    with (
        caplog.at_level("WARNING"),
        pytest.raises(XcuitestChannelError, match="did not come up") as excinfo,
    ):
        env.start(eff, Preconditions())
    message = str(excinfo.value)
    assert "attempt 1/2" in message and "attempt 2/2" in message  # exactly two attempts
    assert "exited (code 71)" in message  # the fail-fast reason reached the loud error
    assert "mid-run crash" not in caplog.text  # the cold-spawn path never claims a mid-run crash
    # The capture is kept, not pruned, so the failure has on-disk evidence past the 20-line tail. (A
    # per-attempt file is port-keyed; both attempts fail-fast, so at least the last one survives.)
    assert list((tmp_path / "logs").glob("runner-*.log"))  # the ephemeral capture was kept


# --- BE-0319: the cold-spawn liveness wait + single retry, off-device via injection --- #
#
# The "await readiness with a liveness check and a bounded retry" seam (units 3-4) is factored so it
# runs without a Simulator: `_Spawned` is a bundle of callables, so a test drives the wait and the
# retry with fakes - the same isolation the channel tests use by injecting a fake transport.


def _fake_spawned(
    *, ready: Any, poll: Any = lambda: None, tail: str = "", discard: Any = lambda: None
) -> _Spawned:
    return _Spawned(driver=object(), ready=ready, poll=poll, log_tail=lambda: tail, discard=discard)


def test_await_cold_runner_returns_none_once_ready() -> None:
    spawned = _fake_spawned(ready=lambda: True)
    assert (
        _await_cold_runner(spawned, timeout=1.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0)
        is None
    )


def test_await_cold_runner_ready_wins_even_if_the_process_has_since_exited() -> None:
    # The probe order is load-bearing: a runner that answered /health is up regardless of its process
    # state, so `ready()` is checked before `poll()`. A health server that answered and then had its
    # `xcodebuild` wrapper exit is a success, not a spurious "the process exited" failure.
    spawned = _fake_spawned(ready=lambda: True, poll=lambda: 71)
    assert (
        _await_cold_runner(spawned, timeout=1.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0)
        is None
    )


def test_await_cold_runner_fails_fast_when_the_xcodebuild_process_exits() -> None:
    # BE-0319 unit 3: a dead xcodebuild aborts the wait at once with its exit code, rather than
    # probing a dead port for the remaining budget — the huge timeout here is never spent.
    spawned = _fake_spawned(ready=lambda: False, poll=lambda: 71)
    reason = _await_cold_runner(
        spawned, timeout=999.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0
    )
    assert reason is not None and "exited (code 71)" in reason


def test_await_cold_runner_times_out_when_never_ready_and_process_alive() -> None:
    # A runner whose process stays alive but never binds its port fails at the deadline (the
    # `health never ready` case), driven by the injected clock so the gate spends no wall time.
    ticks = iter([0.0, 0.0, 0.3])  # deadline = 0.0 + 0.2; the second poll is past it
    spawned = _fake_spawned(ready=lambda: False, poll=lambda: None)
    reason = _await_cold_runner(
        spawned, timeout=0.2, poll=0.0, sleep=lambda _s: None, clock=lambda: next(ticks)
    )
    assert reason is not None and "health never ready within 0.2s" in reason


def test_spawn_cold_retries_once_then_succeeds() -> None:
    # BE-0319 unit 4: a one-off cold-start blip (the first attempt's process dies) is absorbed by a
    # single retry; the second attempt comes up and its driver is returned. Exactly two spawns.
    spawns = 0
    discards: list[int] = []

    def spawn() -> _Spawned:
        nonlocal spawns
        spawns += 1
        n = spawns
        first = n == 1
        return _Spawned(
            driver=f"driver-{n}",
            ready=(lambda: not first),  # first attempt never becomes ready
            poll=(lambda: 1 if first else None),  # first attempt's process died
            log_tail=lambda: "",
            discard=lambda: discards.append(n),
        )

    result = _spawn_cold_with_retry(
        spawn, timeout=1.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0
    )
    assert result.driver == "driver-2"
    assert spawns == 2 and discards == [
        1
    ]  # the failed first attempt discarded, the live second kept


def test_spawn_cold_retries_after_a_timeout_then_succeeds() -> None:
    # The retry absorbs a first attempt that *times out* (process alive, never binds its port), not
    # only one that dies: an advancing clock lets the first attempt cross its deadline, then the
    # second is ready at once. Each attempt re-derives its own deadline, so this exercises a distinct
    # shape from the die-then-succeed case above.
    spawns = 0

    def spawn() -> _Spawned:
        nonlocal spawns
        spawns += 1
        first = spawns == 1
        return _Spawned(
            driver=f"driver-{spawns}",
            ready=lambda: not first,  # first never becomes ready (times out); second ready at once
            poll=lambda: None,  # the process stays alive throughout — no fail-fast
            log_tail=lambda: "",
            discard=lambda: None,
        )

    # attempt 1: deadline 0.0+0.2, crosses at 0.3; attempt 2: ready on the first probe
    ticks = iter([0.0, 0.0, 0.3, 0.0])
    result = _spawn_cold_with_retry(
        spawn, timeout=0.2, poll=0.0, sleep=lambda _s: None, clock=lambda: next(ticks)
    )
    assert result.driver == "driver-2" and spawns == 2


def test_spawn_cold_fails_loudly_after_exactly_two_attempts_with_both_tails() -> None:
    # A repeatable failure (a broken build) fails every attempt and still stops the gate (BE-0049);
    # the loud error carries each attempt's captured tail (unit 2), and there are exactly two — the
    # retry is bounded to one, never unbounded.
    spawns = 0

    def spawn() -> _Spawned:
        nonlocal spawns
        spawns += 1
        n = spawns
        return _Spawned(
            driver=None,
            ready=lambda: False,
            poll=lambda: 65,  # xcodebuild exited on every attempt
            log_tail=lambda: f"\n<<tail-{n}>>",
            discard=lambda: None,
        )

    with pytest.raises(XcuitestChannelError) as excinfo:
        _spawn_cold_with_retry(
            spawn, timeout=1.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0
        )
    message = str(excinfo.value)
    assert spawns == 2  # bounded to a single retry: exactly two attempts, no more
    assert "attempt 1/2" in message and "attempt 2/2" in message
    assert "<<tail-1>>" in message and "<<tail-2>>" in message  # each attempt's tail is folded in
    assert "exited (code 65)" in message  # the dead-process reason (unit 3) reaches the error
