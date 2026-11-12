"""`setLocation` on Android takes one of two routes (BE-0481).

A local emulator keeps the console's `emu geo fix`; every other target — a real device, or an
emulator reached with `adb connect` — mocks the location through the app's BajutsuAndroid receiver
after an `appops` grant. Fast gate over a scripted `adb` (no device): the route choice and its cached
probe, the record the environment keeps before the grant, the re-send after each launch that would
otherwise drop the mock (a relaunch, a crawl reset, the launch after an `installApp`), and the order
and tolerance of the lease's teardown and a device-group member's end.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from bajutsu.common import platform_lifecycle
from bajutsu.common.backend_cli import adb
from bajutsu.common.config import AndroidConfig, Effective, ResolvedPreconditions
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.scenario import Redact, Relaunch, Scenario

_PKG = "com.example.app"
_AUTH = "com.example.auth"
_DEVICE = "R58N1234ABC"
_EMULATOR = "emulator-5554"


def _eff(package: str = _PKG, app_path: str | None = None) -> Effective:
    return Effective(
        target=package,
        platform_config=AndroidConfig(package=package, app_path=app_path),
        backend=["android"],
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        capture=[],
        redact=Redact(),
    )


class _Adb:
    """A scripted `adb`: records every argv and answers as an emulator image or a real device would."""

    def __init__(self, *, emulator_image: bool = False, location_result: int = 1) -> None:
        self.calls: list[list[str]] = []
        self.emulator_image = emulator_image
        self.location_result = location_result
        self.running: set[str] = set()

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "sys.boot_completed" in args:
            return "1\n"
        if "getprop" in args:
            return "1\n" if self.emulator_image and args[-1] in adb.EMULATOR_PROPS else "\n"
        if "resolve-activity" in args:
            return f"{args[-1]}/.Main\n"
        if adb.LOCATION_ACTION in args:
            return f"Broadcast completed: result={self.location_result}"
        if "-n" in args and "start" in args:
            self.running.add(args[args.index("-n") + 1].split("/")[0])
        if "force-stop" in args:
            self.running.discard(args[-1])
        if "pidof" in args:
            if args[-1] in self.running:
                return "1234\n"
            raise subprocess.CalledProcessError(1, args)
        return ""

    def location_calls(self) -> list[list[str]]:
        """The calls that touch the mock location or stop an app, in order."""
        keep = ("appops", adb.LOCATION_ACTION, "force-stop", "geo")
        return [argv for argv in self.calls if any(k in argv for k in keep)]


def _el(identifier: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": identifier,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


def _set(serial: str) -> list[list[str]]:
    return [
        adb.mock_location_appop_cmd(serial, _PKG, "allow"),
        adb.location_broadcast_cmd(serial, _PKG, "set", 35.681, 139.767),
    ]


def _release(serial: str, package: str = _PKG) -> list[list[str]]:
    return [
        adb.location_broadcast_cmd(serial, package, "clear"),
        adb.mock_location_appop_cmd(serial, package, "default"),
    ]


# --- the route --------------------------------------------------------------------------------------


def test_a_local_emulator_keeps_the_console_and_probes_once() -> None:
    run = _Adb(emulator_image=True)
    control = platform_lifecycle.android_device_control(_EMULATOR, _PKG, env_run=run)
    control.set_location(35.681, 139.767)
    control.set_location(1.0, 2.0)
    assert [argv for argv in run.calls if "getprop" in argv] == [
        adb.get_prop_cmd(_EMULATOR, "ro.boot.qemu")
    ]
    assert run.location_calls() == [
        adb.geo_fix_cmd(_EMULATOR, 35.681, 139.767),
        adb.geo_fix_cmd(_EMULATOR, 1.0, 2.0),
    ]


@pytest.mark.parametrize(
    ("serial", "emulator_image"),
    [
        (_DEVICE, False),  # a real device
        ("127.0.0.1:5555", True),  # an emulator reached with `adb connect`: no console to reach
        (_EMULATOR, False),  # a device that merely carries an emulator's serial
    ],
)
def test_every_other_target_takes_the_in_app_route(serial: str, emulator_image: bool) -> None:
    run = _Adb(emulator_image=emulator_image)
    recorded: list[tuple[float, float]] = []
    control = platform_lifecycle.android_device_control(
        serial, _PKG, env_run=run, record_mock_location=lambda lat, lon: recorded.append((lat, lon))
    )
    control.set_location(35.681, 139.767)
    assert run.location_calls() == _set(serial)
    assert recorded == [(35.681, 139.767)]


def test_the_record_is_written_before_a_broadcast_that_fails() -> None:
    # The grant ran, so the teardown must still revoke it even though the step itself failed.
    run = _Adb(location_result=0)
    recorded: list[tuple[float, float]] = []
    control = platform_lifecycle.android_device_control(
        _DEVICE,
        _PKG,
        env_run=run,
        record_mock_location=lambda lat, lon: recorded.append((lat, lon)),
    )
    with pytest.raises(adb.DeviceError, match=r"Bajutsu\.startLocation"):
        control.set_location(35.681, 139.767)
    assert recorded == [(35.681, 139.767)]


# --- the lease ---------------------------------------------------------------------------------------


def _started(run: _Adb, serial: str = _DEVICE) -> AndroidEnvironment:
    env = AndroidEnvironment("adb", serial, adb_run=run)
    env.start(_eff(), ResolvedPreconditions())
    run.calls.clear()
    return env


def _mock(env: AndroidEnvironment, package: str = _PKG) -> None:
    control = env.controller(_eff(package))
    assert control is not None
    control.set_location(35.681, 139.767)


def test_teardown_clears_then_revokes_before_the_force_stop() -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert run.location_calls() == [*_release(_DEVICE), adb.force_stop_cmd(_DEVICE, _PKG)]


def test_teardown_revokes_and_stops_even_when_the_clear_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    run.calls.clear()
    run.location_result = 0  # the app is gone, so no receiver answers the clear
    with caplog.at_level(logging.WARNING):
        env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert run.location_calls() == [*_release(_DEVICE), adb.force_stop_cmd(_DEVICE, _PKG)]
    assert "could not clear the mock location" in caplog.text


def test_a_lease_that_never_mocked_touches_no_app_op() -> None:
    run = _Adb(emulator_image=True)
    env = _started(run, _EMULATOR)
    _mock(env)  # the console route records nothing
    run.calls.clear()
    env.teardown(AdbDriver(_EMULATOR, run=run), _eff())
    assert run.location_calls() == [adb.force_stop_cmd(_EMULATOR, _PKG)]


def test_the_next_lease_starts_without_the_previous_record() -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert run.location_calls() == [adb.force_stop_cmd(_DEVICE, _PKG)]


def test_a_relaunch_re_sends_the_location_after_the_launch() -> None:
    run = _Adb()
    env = AndroidEnvironment("adb", _DEVICE, adb_run=run)
    driver = env.start(_eff(), ResolvedPreconditions())
    _mock(env)
    run.calls.clear()
    env.relauncher(_eff(), Scenario(name="t", steps=[]), driver)(Relaunch())
    launch = next(i for i, argv in enumerate(run.calls) if "start" in argv and "am" in argv)
    assert run.calls[launch + 1 : launch + 3] == _set(_DEVICE)


def test_a_crawl_reset_re_sends_the_location() -> None:
    run = _Adb()
    env = AndroidEnvironment("adb", _DEVICE, adb_run=run)
    driver = env.start(_eff(), ResolvedPreconditions())
    _mock(env)
    run.calls.clear()
    env.crawl_reset(_eff())(driver)
    assert run.location_calls() == [adb.force_stop_cmd(_DEVICE, _PKG), *_set(_DEVICE)]


def test_the_launch_after_an_install_app_re_sends_the_location(tmp_path: Path) -> None:
    # `installApp` force-stops (and may uninstall) the app, taking its providers and grant along; the
    # receiver needs a live process, so the location comes back with the launch that follows.
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = AndroidEnvironment("adb", _DEVICE, adb_run=run)
    env.start(_eff(), ResolvedPreconditions())
    # A screen that is ready at once, so the readiness wait the launch ends with returns at once.
    env._drivers[_PKG] = env._primary_driver = FakeDriver([_el("home"), _el("ok")])
    _mock(env)
    env.install_member(_eff(_PKG, str(apk)), keep_data=False)
    assert run.location_calls()[-1] == adb.force_stop_cmd(_DEVICE, _PKG)  # nothing sent yet
    run.calls.clear()
    control = env.controller(_eff())
    assert control is not None
    control.foreground()
    assert run.location_calls() == _set(_DEVICE)


def test_a_member_s_end_releases_its_own_mock_before_its_stop() -> None:
    run = _Adb()
    env = _started(run)
    _mock(env, _AUTH)
    run.calls.clear()
    env.end_member(AdbDriver(_DEVICE, run=run), _eff(_AUTH))
    assert run.location_calls() == [*_release(_DEVICE, _AUTH), adb.force_stop_cmd(_DEVICE, _AUTH)]
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert run.location_calls() == [adb.force_stop_cmd(_DEVICE, _PKG)]  # nothing left to undo


def test_a_member_start_re_sends_its_recorded_location(tmp_path: Path) -> None:
    apk = tmp_path / "auth.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = _started(run)
    _mock(env, _AUTH)
    run.calls.clear()
    env.start_member(_eff(_AUTH, str(apk)), ResolvedPreconditions(reinstall="overwrite"))
    launch = next(i for i, argv in enumerate(run.calls) if "start" in argv and "am" in argv)
    assert run.calls[launch + 1 : launch + 3] == [
        adb.mock_location_appop_cmd(_DEVICE, _AUTH, "allow"),
        adb.location_broadcast_cmd(_DEVICE, _AUTH, "set", 35.681, 139.767),
    ]


def test_the_latest_location_is_the_one_re_sent() -> None:
    run = _Adb()
    env = AndroidEnvironment("adb", _DEVICE, adb_run=run)
    driver = env.start(_eff(), ResolvedPreconditions())
    control = env.controller(_eff())
    assert control is not None
    control.set_location(1.0, 2.0)
    control.set_location(35.681, 139.767)
    run.calls.clear()
    env.relauncher(_eff(), Scenario(name="t", steps=[]), driver)(Relaunch())
    assert [argv for argv in run.calls if adb.LOCATION_ACTION in argv] == [_set(_DEVICE)[1]]


def test_a_re_send_that_fails_fails_the_relaunch() -> None:
    # A relaunch that silently lost the mock would leave the next step reading the real location.
    run = _Adb()
    env = AndroidEnvironment("adb", _DEVICE, adb_run=run)
    driver = env.start(_eff(), ResolvedPreconditions())
    _mock(env)
    run.location_result = 0
    with pytest.raises(adb.DeviceError, match=r"Bajutsu\.startLocation"):
        env.relauncher(_eff(), Scenario(name="t", steps=[]), driver)(Relaunch())


def test_the_in_app_route_probes_once_too() -> None:
    run = _Adb()
    control = platform_lifecycle.android_device_control(_EMULATOR, _PKG, env_run=run)
    control.set_location(1.0, 2.0)
    control.set_location(3.0, 4.0)
    assert len([argv for argv in run.calls if "getprop" in argv]) == len(adb.EMULATOR_PROPS)


def test_teardown_releases_every_recorded_package() -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    _mock(env, _AUTH)
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    # The member's app is stopped too: the clear launched it, since its `end_member` never ran.
    assert run.location_calls() == [
        *_release(_DEVICE),
        *_release(_DEVICE, _AUTH),
        adb.force_stop_cmd(_DEVICE, _PKG),
        adb.force_stop_cmd(_DEVICE, _AUTH),
    ]


def test_teardown_stops_the_app_even_when_the_revoke_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    run.calls.clear()
    plain = run.__call__

    def revoke_fails(args: list[str]) -> str:
        if "appops" in args and "default" in args:
            run.calls.append(args)
            raise subprocess.CalledProcessError(255, args)
        return plain(args)

    env._run = revoke_fails
    with caplog.at_level(logging.WARNING):
        env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert run.location_calls() == [*_release(_DEVICE), adb.force_stop_cmd(_DEVICE, _PKG)]
    assert "could not revoke the mock-location app-op" in caplog.text


def test_a_member_s_end_tolerates_a_failed_clear(caplog: pytest.LogCaptureFixture) -> None:
    run = _Adb()
    env = _started(run)
    _mock(env, _AUTH)
    run.calls.clear()
    run.location_result = 0
    with caplog.at_level(logging.WARNING):
        env.end_member(AdbDriver(_DEVICE, run=run), _eff(_AUTH))
    assert run.location_calls() == [*_release(_DEVICE, _AUTH), adb.force_stop_cmd(_DEVICE, _AUTH)]
    assert "could not clear the mock location" in caplog.text


@pytest.mark.parametrize(
    ("serial", "expected"),
    [
        ("emulator-5554", True),
        ("emulator-", False),
        ("emulator-5554:5555", False),
        ("xemulator-5554", False),
        ("emulator-55a", False),
    ],
)
def test_is_emulator_serial_needs_the_whole_serial(serial: str, expected: bool) -> None:
    assert adb.is_emulator_serial(serial) is expected


def _launched(run: _Adb, package: str) -> bool:
    return any(
        "-n" in argv and argv[argv.index("-n") + 1].startswith(f"{package}/") for argv in run.calls
    )


def test_teardown_launches_a_stopped_app_to_clear_its_providers() -> None:
    # The providers outlive the process, and only the app can remove them (BE-0481).
    run = _Adb()
    env = _started(run)
    _mock(env)
    run.running.discard(_PKG)  # the scenario ended with the app stopped
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    launch = next(i for i, argv in enumerate(run.calls) if "-n" in argv and "start" in argv)
    clear = run.calls.index(adb.location_broadcast_cmd(_DEVICE, _PKG, "clear"))
    assert launch < clear
    assert run.location_calls() == [*_release(_DEVICE), adb.force_stop_cmd(_DEVICE, _PKG)]


def test_teardown_does_not_relaunch_a_running_app() -> None:
    run = _Adb()
    env = _started(run)
    _mock(env)
    run.calls.clear()
    env.teardown(AdbDriver(_DEVICE, run=run), _eff())
    assert not _launched(run, _PKG)


def test_a_non_finite_coordinate_fails_before_the_device_is_touched() -> None:
    with pytest.raises(adb.DeviceError, match="finite"):
        adb.location_broadcast_cmd(_DEVICE, _PKG, "set", float("nan"), 1.0)
