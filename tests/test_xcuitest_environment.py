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
import re
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bajutsu import backends, simctl
from bajutsu.config import Effective, load_config, resolve
from bajutsu.drivers.xcuitest import XcuitestChannelError
from bajutsu.platform_lifecycle.environments.xcuitest import (
    _MAX_WARM_REUSES,
    _MAX_WARM_REUSES_ENV,
    _RECOVERY_TIMEOUT,
    _RESPAWN_TIMEOUT_ENV,
    _RUNNER_STARTUP_TIMEOUT,
    _RUNNER_STARTUP_TIMEOUT_ENV,
    _WARM_HEALTH_TIMEOUT,
    XcuitestEnvironment,
    _AttemptFailure,
    _await_cold_runner,
    _destination,
    _Recovery,
    _recovery_timeout,
    _respawn_timeout,
    _run_ended_probe,
    _runner_startup_timeout,
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


def test_a_real_device_never_enters_the_recovery_ladder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A real device is powered on out of band and is not listed by `simctl list devices`, so a probe
    # would read it as "gone" and mint a Simulator to replace it — finishing the run on a device the
    # target never named. The ladder is Simulator-only, and this is what says so.
    simctl_calls: list[list[str]] = []

    def _run(argv: list[str], env: object = None) -> str:
        simctl_calls.append(argv)
        return ""

    class _DeadProc:
        pid = 9999

        def poll(self) -> int:
            return 70  # exits at once: both attempts fail, so `recover` runs in between

        def wait(self, timeout: float | None = None) -> int:
            return 0

    class _Driver:
        def health_ready(self) -> bool:
            return False  # /health never answers ready

        def await_ready(self, timeout: float = 10.0) -> None: ...

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _DeadProc())
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _Driver())
    _patch_group_signals(monkeypatch)
    env = XcuitestEnvironment("xcuitest", _DEVICE_UDID, env_run=_run)
    with pytest.raises(XcuitestChannelError, match="did not come up"):
        env.start(_device_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    assert simctl_calls == []  # no probe, no reboot, and above all no `simctl create`
    assert env.replaced_device() is None


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
    """A fake runner process: `poll()` reports liveness, `terminate()`/`kill()` end it.

    Each instance registers itself in `_FAKE_PROCS` under a unique pid so `_patch_group_signals` can
    route a group signal back to it — the discard signals the runner's *process group*, not the bare
    process, so a fake that only implemented `terminate()` would never see the signal at all.
    """

    _next_pid = 9000

    def __init__(self) -> None:
        self.alive = True
        self.terminated = (
            False  # observed by the mid-run-crash test (a dead runner is not signalled)
        )
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        _FAKE_PROCS[self.pid] = self

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.alive = False


_FAKE_PROCS: dict[int, _FakeProc] = {}


def _patch_group_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the discard's process-group signals to the `_FakeProc` they name.

    Every fake process is its own group leader here (`getpgid` is the identity), so a SIGTERM to the
    group ends exactly the fake that was spawned. Without this the real `os.killpg` would signal
    whatever group on the test host happens to carry the fake's pid.
    """
    monkeypatch.setattr("os.getpgid", lambda pid: pid)

    def _killpg(pgid: int, sig: int) -> None:
        proc = _FAKE_PROCS.get(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.terminate() if sig == signal.SIGTERM else proc.kill()

    monkeypatch.setattr("os.killpg", _killpg)


def _write_runner(tmp_path: Path) -> Path:
    runner = tmp_path / "Runner.xctestrun"
    with runner.open("wb") as f:
        plistlib.dump({"Target": {"TestingEnvironmentVariables": {}}}, f)
    return runner


def _globals_plist(locale: str | None) -> str:
    """The device's global preference domain as `defaults export` renders it (BE-0320).

    `None` models a device that carries no pinned language — an unwritten domain — which is what a
    fresh fake device starts from.
    """
    if locale is None:
        return plistlib.dumps({}).decode()
    language, _, _ = locale.partition("_")
    return plistlib.dumps({"AppleLanguages": [language], "AppleLocale": locale}).decode()


def _fake_toolchain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    wedged: dict[str, bool] | None = None,
    system_locale: dict[str, str | None] | None = None,
    export_fails: bool = False,
) -> tuple[list[list[str]], list[list[str]], simctl.RunFn]:
    """Fake Popen (the runner), the driver factory, and simctl; return (popen log, simctl log, run).

    The returned `run` is the fake simctl runner to hand the environment as `env_run`. `wedged`, when
    given, makes the driver's *warm* health probe (`_WARM_HEALTH_TIMEOUT`) raise while `wedged["v"]`
    is True, so a test can wedge the reused runner; the cold-startup `await_ready` (the long timeout)
    always succeeds, so a respawn still comes up.

    `system_locale`, when given, models the device's global preference domain in `["v"]` — the fake
    device answers `defaults export` from it and a `defaults write` updates it, so a test can drive
    the BE-0320 pin the way a real Simulator would answer. Omitted, the domain reads as unwritten and
    every cold spawn pins it. `export_fails` instead makes every read of that domain fail, modelling
    a device whose pin can be written but never confirmed.
    """
    popen_argvs: list[list[str]] = []
    simctl_calls: list[list[str]] = []
    domain: dict[str, str | None] = system_locale if system_locale is not None else {"v": None}

    def _popen(argv: list[str], **_kw: Any) -> _FakeProc:
        popen_argvs.append(argv)
        return _FakeProc()

    class _Driver:
        def await_ready(self, timeout: float = 10.0) -> None:
            if wedged is not None and wedged["v"] and timeout == _WARM_HEALTH_TIMEOUT:
                raise XcuitestChannelError("wedged")

        def health_ready(self) -> bool:
            return True  # the cold runner answers /health at once (BE-0319 unit 3)

    def _run(argv: list[str], env: object = None) -> str:
        simctl_calls.append(argv)
        if argv[2:3] == ["erase"]:
            domain["v"] = None  # a real erase wipes the device's preferences with everything else
        if argv[2:3] == ["list"]:
            # The device listing a cold prep reads to record what kind of device this is, so a
            # replacement can later be cloned from it.
            return _device_json(["UDID"])
        if argv[4:7] == ["defaults", "export", "-globalDomain"]:
            if export_fails:
                raise subprocess.CalledProcessError(1, argv, stderr="device not booted")
            return _globals_plist(domain["v"])
        if argv[4:8] == ["defaults", "write", "-globalDomain", "AppleLocale"]:
            domain["v"] = argv[-1]
        return ""

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _Driver())
    _patch_group_signals(monkeypatch)
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


# --- pinning the Simulator's own system language (BE-0320) --- #
#
# SpringBoard owns the permission prompts `handleSystemAlert` taps by label, and no app launch
# argument reaches it — so a cold spawn writes the device's global preference domain and reboots,
# and a warm reuse is gated on the resolved locale still matching. Same fake points as above.


def _sim_eff_locale(*, test_runner: str, locale: str) -> Effective:
    cfg = (
        f"defaults:\n  locale: {locale}\n"
        f"targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      testRunner: {test_runner}\n"
    )
    return resolve(load_config(cfg), "s")


def _verbs(simctl_calls: list[list[str]]) -> list[str]:
    return [c[2] for c in simctl_calls if c[:2] == ["xcrun", "simctl"]]


def test_cold_spawn_pins_the_system_locale_and_reboots_for_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A device on another language is written and rebooted: `simctl spawn` needs a booted device, and
    # a running SpringBoard does not pick a global-domain write up live, so the value only reaches
    # the alert text on the *next* boot. The write must land before the app is installed and launched.
    domain: dict[str, str | None] = {"v": "en_US"}
    _, simctl_calls, run = _fake_toolchain(monkeypatch, system_locale=domain)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(
        _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="ja_JP"), Preconditions()
    )

    assert domain["v"] == "ja_JP"  # the device now carries the configured locale
    assert simctl.system_locale_cmds("UDID", "ja_JP")[0] in simctl_calls
    # boot (the initial one) -> list (recording the device type a replacement would be cloned from)
    # -> spawn (the read, then the two writes) -> shutdown -> boot (the one that re-renders
    # SpringBoard) -> spawn (the read-back that verifies it took).
    assert _verbs(simctl_calls) == [
        "boot",
        "list",
        "spawn",
        "spawn",
        "spawn",
        "shutdown",
        "boot",
        "spawn",
    ]


def test_cold_spawn_skips_the_extra_boot_when_the_device_already_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The common case — a Simulator already on the configured locale, or one an earlier spawn pinned.
    # Reading the domain is enough to know, so a cold spawn pays one read instead of a second boot.
    _, simctl_calls, run = _fake_toolchain(monkeypatch, system_locale={"v": "en_US"})
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(
        _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="en_US"), Preconditions()
    )

    assert _verbs(simctl_calls).count("boot") == 1  # no second boot cycle
    assert not any("write" in c for c in simctl_calls)


def test_a_locale_change_forces_a_cold_respawn_rather_than_a_stale_warm_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Only a cold spawn re-pins and reboots, so reusing the warm runner for a scenario under another
    # locale would run it against the previous scenario's SpringBoard language — the very accident
    # this item removes. The mismatch is a cache miss, exactly as `erase` and a wedged runner are.
    domain: dict[str, str | None] = {"v": "en_US"}
    popen_argvs, simctl_calls, run = _fake_toolchain(monkeypatch, system_locale=domain)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="en_US")

    env.start(eff, Preconditions())
    env.start(eff, Preconditions(locale="ja_JP"))  # the per-scenario override wins over the config

    assert len(popen_argvs) == 2  # the locale change forced a fresh runner
    assert domain["v"] == "ja_JP"
    assert simctl.system_locale_cmds("UDID", "ja_JP")[1] in simctl_calls


def test_the_same_locale_still_reuses_the_warm_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The gate keys on the *resolved* locale, so a scenario whose override merely restates the
    # target's own `locale` is not a mismatch — it still resumes warm (BE-0291 amortization intact).
    popen_argvs, _, run = _fake_toolchain(monkeypatch, system_locale={"v": "en_US"})
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="en_US")

    env.start(eff, Preconditions())
    env.start(eff, Preconditions(locale="en_US"))

    assert len(popen_argvs) == 1


def test_a_pin_that_does_not_take_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A device that reads back another locale after the write and reboot would run the scenario
    # against an alert language nothing predicts — fail rather than proceed (determinism first).
    def run(argv: list[str], env: object = None) -> str:
        return _globals_plist("en_US") if "export" in argv else ""

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="ja_JP")
    with pytest.raises(simctl.DeviceError, match="BE-0320"):
        env.start(eff, Preconditions())


def test_an_erasing_cold_spawn_re_pins_the_wiped_domain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `erase` wipes the device's preferences, so the pin has to run *after* it — moving the pin above
    # the erase would leave the second scenario on whatever language the wiped device boots with.
    domain: dict[str, str | None] = {"v": "ja_JP"}
    _, simctl_calls, run = _fake_toolchain(monkeypatch, system_locale=domain)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="ja_JP")

    env.start(eff, Preconditions(erase=True))

    assert domain["v"] == "ja_JP"  # re-pinned after the wipe, not left to the boot default
    verbs = _verbs(simctl_calls)
    assert verbs.index("erase") < verbs.index("spawn")


def test_an_unconfirmable_pin_runs_on_but_is_not_remembered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A device whose global domain cannot be read back after the reboot is not an observed mismatch,
    # so the run proceeds — but the pin is unconfirmed, so it must not be recorded: warm reuse is
    # gated on it, and remembering it would carry the doubt across every later lease.
    popen_argvs, _, run = _fake_toolchain(monkeypatch, export_fails=True)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    eff = _sim_eff_locale(test_runner=str(_write_runner(tmp_path)), locale="ja_JP")

    env.start(eff, Preconditions())  # no raise: nothing was observed to be wrong
    assert env._pinned_locale is None
    env.start(eff, Preconditions())
    assert (
        len(popen_argvs) == 2
    )  # the unconfirmed pin blocked warm reuse, so the next lease is cold


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


def test_runner_startup_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # A contended CI host can extend the cold-start ceiling without a code change (the ios-e2e
    # workflow raises it to 300s); a blank or malformed value falls back to the compiled default.
    monkeypatch.delenv(_RUNNER_STARTUP_TIMEOUT_ENV, raising=False)
    assert _runner_startup_timeout() == _RUNNER_STARTUP_TIMEOUT
    monkeypatch.setenv(_RUNNER_STARTUP_TIMEOUT_ENV, "300")
    assert _runner_startup_timeout() == 300.0
    monkeypatch.setenv(_RUNNER_STARTUP_TIMEOUT_ENV, "not-a-number")
    assert _runner_startup_timeout() == _RUNNER_STARTUP_TIMEOUT


def test_respawn_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # The respawn readiness ceiling only tightens a respawn's wait, so unset / non-positive / malformed
    # all read as None (fall back to the cold ceiling) — never as zero, which would remove the wait.
    monkeypatch.delenv(_RESPAWN_TIMEOUT_ENV, raising=False)
    assert _respawn_timeout() is None  # unset -> use the cold ceiling for respawns too
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "90")
    assert _respawn_timeout() == 90.0
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "0")
    assert _respawn_timeout() is None  # non-positive -> cold ceiling, not "no readiness wait"
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "not-a-number")
    assert _respawn_timeout() is None


def test_respawn_uses_the_tighter_readiness_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A first bring-up pays the full cold-start ceiling; a respawn (the Simulator already booted, the
    # app installed) pays the tighter respawn ceiling, so a dead runner surfaces fast instead of
    # hanging out the whole cold budget. Both go through `_spawn_cold_with_retry`; only the timeout
    # differs, which is what a mid-run respawn's readiness wait is bounded by.
    monkeypatch.setenv(_RUNNER_STARTUP_TIMEOUT_ENV, "300")
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "90")
    _, _, run = _fake_toolchain(monkeypatch)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))

    seen: list[float] = []
    original = _spawn_cold_with_retry

    def spy(*args: Any, **kwargs: Any) -> _Spawned:
        seen.append(kwargs["timeout"])
        return original(*args, **kwargs)

    # Patch the module-level name `_spawn_cold` resolves against (string target, so no second
    # `import` of a module this file already imports names from).
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._spawn_cold_with_retry", spy
    )

    XcuitestEnvironment("xcuitest", "UDID", env_run=run, respawn=False).start(eff, Preconditions())
    XcuitestEnvironment("xcuitest", "UDID", env_run=run, respawn=True).start(eff, Preconditions())
    assert seen == [300.0, 90.0]  # first bring-up: cold ceiling; respawn: the tighter one


def test_in_place_respawn_uses_the_tighter_readiness_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The common mid-run-crash path keeps the SAME environment: the dead resident stays warm-cached, so
    # the retry reuses this instance and `start` respawns cold *in place*. That in-place respawn must
    # also take the tighter ceiling — the env self-detects it via `_cold_spawned_before`, because the
    # pool's `respawn` flag on a reused instance was fixed to False at first bring-up (see the PR
    # review: without this, the most common recovery path silently paid the full 300s cold ceiling).
    monkeypatch.setenv(_RUNNER_STARTUP_TIMEOUT_ENV, "300")
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "90")
    _, _, run = _fake_toolchain(monkeypatch)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))

    seen: list[float] = []
    original = _spawn_cold_with_retry

    def spy(*args: Any, **kwargs: Any) -> _Spawned:
        seen.append(kwargs["timeout"])
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._spawn_cold_with_retry", spy
    )

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)  # respawn=False: a first bring-up
    env.start(eff, Preconditions())  # first cold spawn: full ceiling
    assert env._runner_proc is not None
    env._runner_proc.alive = False  # type: ignore[attr-defined]  # the runner crashed mid-run
    env.start(eff, Preconditions())  # same instance respawns cold in place: the tighter ceiling
    assert seen == [300.0, 90.0]


def test_erase_forced_cold_spawn_keeps_the_full_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `erase` shuts the Simulator down, so the cold spawn after it is a genuine first-boot start (reboot
    # + reinstall), not a respawn onto a live Simulator — it must keep the full cold ceiling even though
    # this instance has cold-spawned before (`_cold_spawned_before`). Guards the respawn ceiling from
    # wrongly tightening a real cold boot, which needs the whole budget.
    monkeypatch.setenv(_RUNNER_STARTUP_TIMEOUT_ENV, "300")
    monkeypatch.setenv(_RESPAWN_TIMEOUT_ENV, "90")
    _, _, run = _fake_toolchain(monkeypatch)
    eff = _sim_eff(test_runner=str(_write_runner(tmp_path)))

    seen: list[float] = []
    original = _spawn_cold_with_retry

    def spy(*args: Any, **kwargs: Any) -> _Spawned:
        seen.append(kwargs["timeout"])
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._spawn_cold_with_retry", spy
    )

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(eff, Preconditions())  # first cold spawn: full ceiling
    env.start(eff, Preconditions(erase=True))  # erase reboots the Simulator: a genuine cold start
    assert seen == [300.0, 300.0]  # the erase spawn keeps the full ceiling, not the respawn one


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


def test_discarding_a_crashed_runner_warns_and_sweeps_its_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The diagnostic seam: a runner that exited on its own (the known app.launch()-cycle crash) is
    # discarded without a terminate() — the leader's pid is already reaped — and logs a mid-run-crash
    # warning so a run that died on a `Connection refused` shows *why* the channel went away. It is
    # still signalled at the group level, though: an XCTest-host child can outlive the leader and keep
    # holding the device's automation session, so the crashed branch sweeps the group by pid-as-pgid.
    _, _, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    proc = env._runner_proc
    assert proc is not None
    proc.alive = False  # type: ignore[attr-defined]  # the runner crashed mid-run
    log = env._runner_log
    assert log is not None and log.exists()
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: signalled.append((pgid, sig)))
    with caplog.at_level("WARNING"):
        env._discard_runner()
    assert not proc.terminated  # type: ignore[attr-defined]  # terminate() is never called: the
    # leader's pid is already reaped, so there is nothing to signal directly
    assert signalled == [(proc.pid, signal.SIGKILL)]  # the group is swept instead
    assert "exited on its own" in caplog.text
    # A review finding this PR caught: the warning tells the operator to "see <path>" — that file
    # must still exist, or the pointer resolves to nothing. A mid-run crash keeps its ephemeral
    # capture (parity with a failed cold-spawn attempt), never prunes it.
    assert f"see {log}" in caplog.text
    assert log.exists()


def test_a_discarded_cold_attempt_terminates_the_app_under_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The other half of BE-XXXX's motivation: an app left mid-launch by the timeout that failed one
    # attempt is exactly what the next attempt would call `launch()` on again, so a discard on the
    # retry path — no teardown in the picture — must terminate the app under test itself, not merely
    # the runner process.
    _, simctl_calls, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    simctl_calls.clear()
    env._discard_runner(warn_on_crash=False, keep_log=True)
    assert ["xcrun", "simctl", "terminate", "UDID", "com.x"] in simctl_calls


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


def test_a_kept_default_capture_is_logged_at_the_moment_it_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A retry that then succeeds never raises, so `_spawn_cold_with_retry`'s folded diagnostics (only
    # built when *every* attempt fails) never mention the failed attempt's own capture. Without this
    # info line, that file becomes untracked the instant `_runner_log` moves on to the next attempt —
    # orphaned in `_DEFAULT_RUNNER_LOG_DIR` with nothing pointing at it.
    _, _, run = _fake_toolchain(monkeypatch)
    monkeypatch.delenv("BAJUTSU_XCUITEST_RUNNER_LOG", raising=False)
    monkeypatch.setattr(
        "bajutsu.platform_lifecycle.environments.xcuitest._DEFAULT_RUNNER_LOG_DIR",
        tmp_path / "default-logs",
    )
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    log = env._runner_log
    assert log is not None and log.exists()
    with caplog.at_level("INFO"):
        env._discard_runner(warn_on_crash=False, keep_log=True)  # the failed-attempt discard path
    assert log.exists()  # kept, not pruned — same as a repeatable failure's evidence
    assert "kept a failed attempt's capture" in caplog.text
    assert str(log) in caplog.text  # discoverable: the path is named, not just "kept something"


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
        pid = (
            12345  # a real Popen always has one; the discard's group sweep reads it even when dead
        )

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

    def _run(argv: list[str], env: object = None) -> str:
        # A device already on the configured locale, so the BE-0320 pin is a no-op here and this
        # test still exercises only the cold-spawn retry it is about.
        return _globals_plist("en_US") if "export" in argv else ""

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: _DeadProc())
    monkeypatch.setattr(backends, "make_driver", lambda *_a, **_k: _Driver())
    _patch_group_signals(
        monkeypatch
    )  # the crashed branch sweeps by pgid; never signal a real group
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
    *,
    ready: Any,
    poll: Any = lambda: None,
    tail: str = "",
    discard: Any = lambda: None,
    run_ended: Any = lambda: None,
) -> _Spawned:
    return _Spawned(
        driver=object(),
        ready=ready,
        poll=poll,
        log_tail=lambda: tail,
        discard=discard,
        run_ended=run_ended,
    )


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
    failure = _await_cold_runner(
        spawned, timeout=999.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0
    )
    assert failure is not None and "exited (code 71)" in failure.detail
    # The kind is what the recovery ladder keys on, so it must not depend on parsing the prose.
    assert failure.kind == "process-exit"


def test_run_ended_probe_reports_nothing_while_the_run_is_still_going(tmp_path: Path) -> None:
    log = tmp_path / "runner.log"
    log.write_bytes(b"Running tests...\n    t = 0.81s Launch com.example.app\n")
    assert _run_ended_probe(log)() is None


def test_run_ended_probe_detects_the_marker_appended_after_an_earlier_clean_probe(
    tmp_path: Path,
) -> None:
    # The real shape: the capture grows under the probe, so a marker written *after* a probe that saw
    # a healthy log must still be found by the next one.
    log = tmp_path / "runner.log"
    log.write_bytes(b"Running tests...\n")
    probe = _run_ended_probe(log)
    assert probe() is None
    with log.open("ab") as fh:
        fh.write(b"Test Suite 'All tests' failed at 2026-07-30 05:22:30.761.\n")
    reason = probe()
    assert reason is not None and "Test Suite 'All tests' failed" in reason


def test_run_ended_probe_finds_a_marker_split_across_two_reads(tmp_path: Path) -> None:
    # Each probe reads only what was appended, so a marker straddling two reads would be invisible
    # without the carried overlap.
    log = tmp_path / "runner.log"
    marker = b"Test Suite 'All tests' failed"
    log.write_bytes(marker[:10])
    probe = _run_ended_probe(log)
    assert probe() is None
    with log.open("ab") as fh:
        fh.write(marker[10:] + b" at 2026-07-30 05:22:30.761.\n")
    assert probe() is not None


def test_run_ended_probe_sticks_the_launch_timeout_cause_across_reads(tmp_path: Path) -> None:
    # The launch timeout is logged well before the suite reports failure (BE-XXXX unit 1's premise),
    # so the two markers rarely land in the same read window — the flag has to persist across probe
    # calls to still name the cause, the signal a reader needs to tell "this device needs rebooting"
    # from "this build is broken".
    log = tmp_path / "runner.log"
    log.write_bytes(b"")
    probe = _run_ended_probe(log)
    with log.open("ab") as fh:
        fh.write(
            b"<unknown>:0: error: Failed to launch com.example.app: "
            b"Timed out attempting to launch app.\n"
        )
    assert probe() is None  # the app launch timed out, but the xctest run has not ended yet
    with log.open("ab") as fh:
        fh.write(b"Test Suite 'All tests' failed at 2026-07-30 05:22:30.761.\n")
    reason = probe()
    assert reason is not None and "after the app launch timed out" in reason


def test_run_ended_probe_names_no_cause_without_a_launch_timeout(tmp_path: Path) -> None:
    # The negative case: a suite failure with no preceding launch-timeout marker gets no cause suffix,
    # so the sticky flag pins the specific signature rather than firing on every run-ended failure.
    log = tmp_path / "runner.log"
    log.write_bytes(b"Test Suite 'All tests' failed at 2026-07-30 05:22:30.761.\n")
    reason = _run_ended_probe(log)()
    assert reason is not None and "after the app launch timed out" not in reason


def test_run_ended_probe_is_quiet_when_the_capture_does_not_exist(tmp_path: Path) -> None:
    # A spawn that failed before writing anything must not be judged by a missing file.
    assert _run_ended_probe(tmp_path / "absent.log")() is None
    assert _run_ended_probe(None)() is None


def test_await_cold_runner_fails_fast_when_the_test_run_ended_though_the_process_lives() -> None:
    # The gap the process-liveness check alone cannot see: `xcodebuild` finished its test run (the
    # app-launch timeout on the ios-e2e lane) but lingers, so `poll()` stays None and the wait would
    # otherwise run to the ceiling. The capture's terminal marker ends it at once — the huge timeout
    # here is never spent.
    spawned = _fake_spawned(
        ready=lambda: False, poll=lambda: None, run_ended=lambda: "the xctest run ended (x)"
    )
    # The clock advances per probe, so a wait that ignored the marker would end at the ceiling
    # instead of spinning — the detection's absence fails this loudly rather than hanging the gate.
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += 100.0
        return now

    failure = _await_cold_runner(
        spawned, timeout=999.0, poll=0.0, sleep=lambda _s: None, clock=clock
    )
    assert failure is not None and "the xctest run ended" in failure.detail
    assert failure.kind == "run-ended"
    assert now == 100.0  # only the deadline was read: the marker ended the wait on the first round


def test_await_cold_runner_ready_wins_over_an_ended_run() -> None:
    # Probe order again: a runner answering /health is up, so a marker that landed in the same round
    # (a suite line racing the health server) must not turn a live runner into a failure.
    spawned = _fake_spawned(ready=lambda: True, run_ended=lambda: "the xctest run ended (x)")
    assert (
        _await_cold_runner(spawned, timeout=1.0, poll=0.0, sleep=lambda _s: None, clock=lambda: 0.0)
        is None
    )


def test_await_cold_runner_times_out_when_never_ready_and_process_alive() -> None:
    # A runner whose process stays alive but never binds its port fails at the deadline (the
    # `health never ready` case), driven by the injected clock so the gate spends no wall time.
    ticks = iter([0.0, 0.0, 0.3])  # deadline = 0.0 + 0.2; the second poll is past it
    spawned = _fake_spawned(ready=lambda: False, poll=lambda: None)
    failure = _await_cold_runner(
        spawned, timeout=0.2, poll=0.0, sleep=lambda _s: None, clock=lambda: next(ticks)
    )
    assert failure is not None and "health never ready within 0.2s" in failure.detail
    assert failure.kind == "never-ready"


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


def test_spawn_cold_does_not_retry_after_a_timeout() -> None:
    # With nothing about the device changed (the default no-op recovery), the startup ceiling is a
    # *total* budget shared across attempts: a first attempt that spends the whole ceiling on "health
    # never ready" (process alive, never binds its port) leaves nothing for a retry, so exactly one
    # attempt runs and it fails loudly. A second full-ceiling wait against the same device would
    # double the worst case (300s → 600s on the ios-e2e lane) for no new information. A recovery that
    # repaired the device does earn a fresh ceiling — see the fresh-budget test below.
    spawns = 0

    def spawn() -> _Spawned:
        nonlocal spawns
        spawns += 1
        return _Spawned(
            driver=f"driver-{spawns}",
            ready=lambda: False,  # never binds its port
            poll=lambda: None,  # the process stays alive — the timeout path, not fail-fast
            log_tail=lambda: "",
            discard=lambda: None,
        )

    # outer deadline 0.0+0.2; attempt 1's own wait crosses at 0.3 (health never ready); the retry
    # check then sees the shared budget spent (0.3 > 0.2) and stops before a second spawn.
    ticks = iter([0.0, 0.0, 0.0, 0.3, 0.3])
    with pytest.raises(XcuitestChannelError) as excinfo:
        _spawn_cold_with_retry(
            spawn, timeout=0.2, poll=0.0, sleep=lambda _s: None, clock=lambda: next(ticks)
        )
    assert spawns == 1  # a spent budget yields no second attempt
    assert "health never ready" in str(excinfo.value)


def test_spawn_cold_retries_after_an_ended_run_because_it_leaves_budget() -> None:
    # The residual BE-0319 left behind, closed. A Simulator app-launch timeout used to hold the wait
    # at the ceiling with the process alive, spending the shared budget and so making the retry
    # structurally unreachable — the one failure the retry existed to absorb. Detecting the ended run
    # returns while the budget is nearly intact, so the second attempt actually runs.
    spawns = 0

    def spawn() -> _Spawned:
        nonlocal spawns
        spawns += 1
        first = spawns == 1
        return _Spawned(
            driver=f"driver-{spawns}",
            ready=(lambda: not first),
            poll=lambda: None,  # the process lingers throughout: only the marker ends attempt 1
            log_tail=lambda: "",
            discard=lambda: None,
            run_ended=(
                lambda: "the xctest run ended (Test Suite 'All tests' failed)" if first else None
            ),
        )

    # Every probe costs wall time, so a wait that ignored the marker would still reach the ceiling
    # and spend the budget — this fails loudly (rather than spinning) if the detection is lost.
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += 10.0
        return now

    result = _spawn_cold_with_retry(
        spawn, timeout=300.0, poll=0.0, sleep=lambda _s: None, clock=clock
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


# --- device recovery between cold-spawn attempts --- #
#
# BE-0319's retry isolates every *host*-side resource per attempt (port, .xctestrun, capture) but
# hands the retry the same device the first attempt failed on. These cover the seam that repairs the
# device in between, and what that does to the retry's budget.


def _failing_spawn(counter: list[int], *, ready_on: int | None = None) -> Callable[[], _Spawned]:
    """A spawn whose Nth attempt is the first to come up (never, when `ready_on` is None)."""

    def spawn() -> _Spawned:
        counter.append(1)
        n = len(counter)
        return _Spawned(
            driver=f"driver-{n}",
            ready=lambda: n == ready_on,
            poll=lambda: None,  # alive throughout: the ended run is what fails each attempt
            log_tail=lambda: "",
            discard=lambda: None,
            run_ended=(
                lambda: (
                    None
                    if n == ready_on
                    else "the xctest run ended (Test Suite 'All tests' failed) after the app launch "
                    "timed out"
                )
            ),
        )

    return spawn


def test_recovery_gets_the_classified_failure_and_runs_between_attempts() -> None:
    # The rung is chosen from the failure's `kind`, so a recovery never has to parse the prose.
    spawns: list[int] = []
    seen: list[str] = []

    def recover(failure: _AttemptFailure) -> _Recovery | None:
        seen.append(failure.kind)
        return _Recovery("rebooted it")

    result = _spawn_cold_with_retry(
        _failing_spawn(spawns, ready_on=2),
        timeout=300.0,
        recover=recover,
        poll=0.0,
        sleep=lambda _s: None,
        clock=lambda: 0.0,
    )
    assert result.driver == "driver-2"
    assert seen == ["run-ended"]  # recovery ran once, between the two attempts


def test_a_repaired_device_earns_a_fresh_ceiling_even_after_a_spent_budget() -> None:
    # The heart of the fix. An app-launch timeout leaves a degraded device, not spare seconds, so the
    # shared budget alone makes the retry unreachable exactly when it is most needed. A recovery that
    # rebooted or replaced the device restarts the ceiling, because the next attempt runs against a
    # device that demonstrably came back up.
    spawns: list[int] = []
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += 100.0  # every probe is expensive: attempt 1 spends the whole 200s ceiling
        return now

    result = _spawn_cold_with_retry(
        _failing_spawn(spawns, ready_on=2),
        timeout=200.0,
        recover=lambda _f: _Recovery("rebooted it", fresh_budget=200.0),
        poll=0.0,
        sleep=lambda _s: None,
        clock=clock,
    )
    assert result.driver == "driver-2" and len(spawns) == 2


def test_a_recovery_that_changed_nothing_keeps_the_shared_budget() -> None:
    # The other half of that contract: a rung that only observed the device (the probe itself failed,
    # or an `xcodebuild` that exited on its own) must not buy a second full wait.
    spawns: list[int] = []
    ticks = iter([0.0, 0.0, 0.0, 0.3, 0.3])
    with pytest.raises(XcuitestChannelError) as excinfo:
        _spawn_cold_with_retry(
            _failing_spawn(spawns),
            timeout=0.2,
            recover=lambda _f: _Recovery("could not probe the device; left it as it is"),
            poll=0.0,
            sleep=lambda _s: None,
            clock=lambda: next(ticks),
        )
    assert len(spawns) == 1
    # The note still reaches the error, so a reader sees the rung ran and chose to do nothing.
    assert "recovery after attempt 1: could not probe the device" in str(excinfo.value)


def test_recovery_notes_are_folded_into_the_loud_failure() -> None:
    spawns: list[int] = []
    with pytest.raises(XcuitestChannelError) as excinfo:
        _spawn_cold_with_retry(
            _failing_spawn(spawns),
            timeout=300.0,
            recover=lambda _f: _Recovery("UDID-old vanished; created replacement UDID-new"),
            poll=0.0,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )
    message = str(excinfo.value)
    assert "attempt 1/2" in message and "attempt 2/2" in message
    assert "recovery after attempt 1: UDID-old vanished; created replacement UDID-new" in message
    # No recovery is attempted after the *last* attempt: there is no further spawn to prepare for.
    assert "recovery after attempt 2" not in message


def test_a_device_that_cannot_be_repaired_fails_the_run() -> None:
    # A host that lost its Simulator runtimes is a device fault, not a flaky spawn: the recovery
    # raises and the error propagates, rather than funding another attempt that cannot work.
    spawns: list[int] = []

    def recover(_f: _AttemptFailure) -> _Recovery | None:
        raise simctl.DeviceError("no iPhone device type is available to replace it")

    with pytest.raises(simctl.DeviceError, match="no iPhone device type"):
        _spawn_cold_with_retry(
            _failing_spawn(spawns),
            timeout=300.0,
            recover=recover,
            poll=0.0,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )
    assert len(spawns) == 1  # the second attempt was never spawned


def test_diagnostics_survive_when_the_device_cannot_be_repaired() -> None:
    # A device fault must not swallow what got the run there: attempt 1's classified reason (the
    # operator's only signal for *why* the runner never answered, BE-0319 unit 2) would otherwise
    # vanish behind the bare DeviceError `recover` raises.
    spawns: list[int] = []

    def recover(_f: _AttemptFailure) -> _Recovery | None:
        raise simctl.DeviceError("no iPhone device type is available to replace it")

    with pytest.raises(simctl.DeviceError) as excinfo:
        _spawn_cold_with_retry(
            _failing_spawn(spawns),
            timeout=300.0,
            recover=recover,
            poll=0.0,
            sleep=lambda _s: None,
            clock=lambda: 0.0,
        )
    message = str(excinfo.value)
    assert "attempt 1/2" in message  # the classified failure that led to the unrepairable device
    assert "recovery after attempt 1 failed: no iPhone device type" in message


# --- the ladder itself: which rung a classified failure picks, against a fake simctl --- #
#
# These drive `_recover_between_attempts` directly: it is the decision the rest of the recovery hangs
# on, and calling it without a spawn keeps each case to the simctl argvs it is actually about.


# The ceiling the failing spawn was given. These cases are all first bring-ups, so it is the cold one;
# the respawn case has its own test below, where the tighter ceiling is the point.
_COLD = _RUNNER_STARTUP_TIMEOUT


def _eff_for_ladder() -> Effective:
    # No appPath, so the re-prepare exercises boot / locale / permissions without needing a built app.
    return _sim_eff(test_runner="/nonexistent.xctestrun")


def _device_json(udids: list[str], *, device_type: str = "com.apple.x.iPhone-17-Pro") -> str:
    import json

    return json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                    {"udid": u, "deviceTypeIdentifier": device_type, "state": "Booted"}
                    for u in udids
                ]
            }
        }
    )


def _ladder_run(present: list[str], *, created: str = "UDID-NEW") -> tuple[list[list[str]], Any]:
    """A fake simctl that lists `present` as available and mints `created` on `simctl create`."""
    import json

    calls: list[list[str]] = []

    def run(argv: list[str], env: object = None) -> str:
        calls.append(argv)
        verb = argv[2:3]
        if verb == ["list"] and argv[3:4] == ["devicetypes"]:
            return json.dumps(
                {
                    "devicetypes": [
                        {
                            "name": "iPhone 17 Pro",
                            "identifier": "com.apple.x.iPhone-17-Pro",
                            "productFamily": "iPhone",
                        }
                    ]
                }
            )
        if verb == ["list"]:
            return _device_json(present)
        if verb == ["create"]:
            present.append(created)
            return f"{created}\n"
        if argv[4:7] == ["defaults", "export", "-globalDomain"]:
            return _globals_plist("en_US")
        return ""

    return calls, run


def _verb_seq(calls: list[list[str]]) -> list[str]:
    return [c[2] for c in calls if c[:2] == ["xcrun", "simctl"]]


def test_a_process_exit_leaves_the_booted_device_alone() -> None:
    # An `xcodebuild` that exited on its own says nothing about the device, and the discard has already
    # terminated the app — so the cheapest rung is the right one, and it buys no fresh budget.
    calls, run = _ladder_run(["UDID"])
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("process-exit", "exited (code 65)"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    assert recovery is not None and recovery.fresh_budget is None
    assert _verb_seq(calls) == ["list"]  # probed, nothing more


def test_an_app_launch_timeout_reboots_and_re_prepares_the_device() -> None:
    # The dominant flake: the device stopped honouring automation, which only a reboot clears. The
    # re-prepare is what puts the app and its permissions back where a scenario expects them.
    calls, run = _ladder_run(["UDID"])
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("run-ended", "the xctest run ended after the app launch timed out"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    assert recovery is not None and recovery.fresh_budget == _runner_startup_timeout()
    assert _verb_seq(calls)[:5] == ["list", "shutdown", "boot", "bootstatus", "boot"]
    assert "rebooted and re-prepared UDID" in recovery.note


def test_a_vanished_device_is_replaced_and_reported_to_the_pool() -> None:
    # The exit-70 case: simctl no longer lists the device, so retrying onto it cannot work. The run
    # continues on a replacement, and `replaced_device()` is how the pool learns to re-key by it.
    calls, run = _ladder_run([])  # the leased device is gone from the listing
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("run-ended", "the xctest run ended"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    assert recovery is not None and recovery.fresh_budget == _runner_startup_timeout()
    assert env._udid == "UDID-NEW" and env.replaced_device() == "UDID-NEW"
    # Created, then booted to completion *before* the prep, so the first boot is not charged to the
    # next attempt's readiness ceiling. The listings ahead of `create` are the probe and the
    # device-type lookup (this environment never cold-prepped, so it captured no type to clone).
    seq = _verb_seq(calls)
    assert seq[0] == "list" and "create" in seq
    assert seq[seq.index("create") + 1] == "bootstatus"
    assert "vanished" in recovery.note and "UDID-NEW" in recovery.note


def test_a_replacement_clones_the_type_captured_while_the_device_was_healthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The device type is recorded at the first cold prep, because by the time a replacement is needed
    # the device is no longer there to ask — so the replacement is the model the run was written
    # against rather than whatever the host happens to offer.
    _, simctl_calls, run = _fake_toolchain(monkeypatch)
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    env.start(_sim_eff(test_runner=str(_write_runner(tmp_path))), Preconditions())
    assert simctl.list_all_devices_cmd() in simctl_calls
    assert env._device_type_id == "com.apple.x.iPhone-17-Pro"

    # With a type already captured, the replacement clones it and never consults `list devicetypes`.
    calls, replace_run = _ladder_run([])
    env._run = replace_run  # type: ignore[assignment]
    env._recover_between_attempts(
        _AttemptFailure("run-ended", "ended"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    # The name leads with the model (the report's device row and `serve`'s `iphone`/`ipad` capability
    # token both read it as one) and carries the replaced udid so recoveries stay tellable apart.
    assert (
        simctl.create_cmd("iPhone 17 Pro (bajutsu-recovered-UDID)", "com.apple.x.iPhone-17-Pro")
        in calls
    )
    assert not any(c[2:4] == ["list", "devicetypes"] for c in calls)


def test_a_probe_that_could_not_run_changes_nothing() -> None:
    # A host too sick to list its devices must not have a device replaced on that evidence.
    calls: list[list[str]] = []

    def run(argv: list[str], env: object = None) -> str:
        calls.append(argv)
        raise OSError("xcrun unavailable")

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("never-ready", "health never ready"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    assert recovery is not None and recovery.fresh_budget is None
    assert env._udid == "UDID" and env.replaced_device() is None
    assert "could not probe" in recovery.note


def test_a_vanished_device_with_no_replaceable_type_fails_loudly() -> None:
    # A host that lost its runtimes along with the device: nothing is left to run on, so this is a
    # device fault rather than another doomed attempt.
    import json

    def run(argv: list[str], env: object = None) -> str:
        if argv[2:4] == ["list", "devicetypes"]:
            return json.dumps({"devicetypes": []})  # no iPhone type at all
        if argv[2:3] == ["list"]:
            return _device_json([])
        return ""

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    with pytest.raises(simctl.DeviceError, match="no device type matching iPhone 15 is available"):
        env._recover_between_attempts(
            _AttemptFailure("run-ended", "ended"),
            _eff_for_ladder(),
            Preconditions(),
            None,
            ceiling=_COLD,
        )


def test_an_ipad_target_is_never_replaced_with_an_iphone() -> None:
    # "Any iPhone beats failing" only holds for an iPhone target. `device_type_identifier` matches
    # simctl's device-type name exactly, and iPad names carry parentheses (e.g. "iPad Pro
    # (12.9-inch) (6th generation)"), so an exact-match miss is ordinary rather than exotic —
    # substituting an iPhone for a missed iPad would finish the run on a layout the scenario was
    # never written against, silently. This must fail loudly instead.
    cfg = (
        'defaults:\n  device: "iPad Pro (12.9-inch) (6th generation)"\n'
        "targets:\n  s:\n    bundleId: com.x\n    xcuitest:\n      testRunner: /nonexistent.xctestrun\n"
    )
    ipad_eff = resolve(load_config(cfg), "s")
    calls, run = _ladder_run([])  # devicetypes lists only "iPhone 17 Pro" — never an iPad match
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    with pytest.raises(
        simctl.DeviceError,
        match=re.escape("no device type matching iPad Pro (12.9-inch) (6th generation)"),
    ):
        env._recover_between_attempts(
            _AttemptFailure("run-ended", "ended"),
            ipad_eff,
            Preconditions(),
            None,
            ceiling=_COLD,
        )
    assert not any(c[2:3] == ["create"] for c in calls)  # no iPhone replacement was minted


def test_a_recovery_that_overran_its_bound_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # A device that takes longer than the bound to come back is not coming back, and a retry funded
    # out of the remaining job time would only fail later.
    monkeypatch.setenv("BAJUTSU_XCUITEST_RECOVERY_TIMEOUT", "0")
    _calls, run = _ladder_run(["UDID"])
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    with pytest.raises(simctl.DeviceError, match="recovery exceeded"):
        env._recover_between_attempts(
            _AttemptFailure("run-ended", "ended"),
            _eff_for_ladder(),
            Preconditions(),
            None,
            ceiling=_COLD,
        )


def test_recovery_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAJUTSU_XCUITEST_RECOVERY_TIMEOUT", "42")
    assert _recovery_timeout() == 42.0
    monkeypatch.setenv("BAJUTSU_XCUITEST_RECOVERY_TIMEOUT", "not-a-number")
    assert _recovery_timeout() == _RECOVERY_TIMEOUT  # an unparseable override keeps the default


def test_a_rebooted_device_keeps_the_ceiling_its_spawn_started_on() -> None:
    # A reboot ends with the device booted and the app reinstalled, which is exactly what the tighter
    # respawn ceiling assumes — so a rebooted respawn keeps that ceiling instead of inflating to the
    # cold budget, which is what kept a mid-run respawn's worst case from tripling.
    _calls, run = _ladder_run(["UDID"])
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("run-ended", "ended"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=90.0,  # the respawn ceiling this lane opted into
    )
    assert recovery is not None and recovery.fresh_budget == 90.0


def test_a_replacement_device_earns_the_full_cold_ceiling() -> None:
    # A device that has never run anything is a genuine first bring-up, however tight the failing
    # spawn's ceiling was: its very first `xcodebuild test-without-building` pays the whole spin-up.
    _calls, run = _ladder_run([])  # the leased device is gone, so a replacement is created
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("run-ended", "ended"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=90.0,
    )
    assert recovery is not None and recovery.fresh_budget == _runner_startup_timeout()
    assert recovery.fresh_budget != 90.0


@pytest.mark.parametrize(
    ("present", "kind"),
    [
        (["UDID"], "process-exit"),  # the rung that deliberately leaves a booted device alone
        ([], "run-ended"),  # ... and the replace rung, for contrast
    ],
)
def test_the_recovery_bound_covers_a_rung_that_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, present: list[str], kind: str
) -> None:
    # The probe that opens the ladder is itself a subprocess, so a host slow enough to blow the bound
    # merely answering `simctl list` must fail the run whatever the rung then decided — otherwise the
    # "whole ladder is bounded" contract holds only on the paths that happen to repair something.
    monkeypatch.setenv("BAJUTSU_XCUITEST_RECOVERY_TIMEOUT", "0")
    _calls, run = _ladder_run(list(present))
    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    with pytest.raises(simctl.DeviceError, match="recovery exceeded"):
        env._recover_between_attempts(
            _AttemptFailure(kind, "why"),  # type: ignore[arg-type]
            _eff_for_ladder(),
            Preconditions(),
            None,
            ceiling=_COLD,
        )


def test_an_unknown_probe_is_still_reported_within_the_bound() -> None:
    # The same do-nothing rung, on a host answering promptly: it reports its note and no fresh budget
    # rather than failing, so the bound above is what distinguishes a sick host from a quiet one.
    def run(argv: list[str], env: object = None) -> str:
        raise OSError("xcrun unavailable")

    env = XcuitestEnvironment("xcuitest", "UDID", env_run=run)
    recovery = env._recover_between_attempts(
        _AttemptFailure("never-ready", "health never ready"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    assert recovery.fresh_budget is None and "could not probe" in recovery.note


def test_a_replacements_name_keeps_its_capability_token_and_report_row() -> None:
    # Two consumers read a device's name as its human model, and a name of ours that dropped the model
    # would break both silently: `serve`'s capability inventory takes the `iphone` / `ipad` class token
    # out of it by substring (a missing token means the hosted router never leases the device for a job
    # that asked for `iphone`), and the report renders it as the device row.
    from bajutsu.serve.capabilities import _device_class_token

    # A udid longer than 8 characters, so a truncated suffix and the full one are distinguishable —
    # the point of the suffix is letting an operator match a "UDID-... vanished" log line against
    # `simctl list` afterwards, which an 8-character prefix cannot support.
    old_udid = "2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA"
    calls, run = _ladder_run([])
    env = XcuitestEnvironment("xcuitest", old_udid, env_run=run)
    env._recover_between_attempts(
        _AttemptFailure("run-ended", "ended"),
        _eff_for_ladder(),
        Preconditions(),
        None,
        ceiling=_COLD,
    )
    created = next(c for c in calls if c[2:3] == ["create"])
    name = created[3]
    assert _device_class_token(name) == "iphone"
    assert "iPhone" in name  # the report's device row shows a model a reader recognizes
    assert (
        f"bajutsu-recovered-{old_udid}" in name
    )  # the *whole* vanished udid, not a truncated prefix
