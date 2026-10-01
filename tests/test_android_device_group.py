"""Tests for the Android side of a device group (BE-0447, unit 6).

Covers the emulator environment's member lifecycle over a scripted `adb` — a starting member's own
reinstall mode without the device-wide clears, a later member's launch-only start, `installApp`'s
install (and the downgrade it names), and `foreground` launching or resuming an app — plus the
driver's front-app check: a member's read that shows another package raises `AppNotInFront`, never
an empty tree, so no check can pass on a screen it was not looking at.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.config import AndroidConfig, Effective
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver, HierarchyRead
from bajutsu.common.drivers.adb.adb_driver import slice_hierarchy_root as slice_root
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.platform_lifecycle.protocols import ReadinessResult
from bajutsu.common.scenario import Preconditions, Redact

_PRIMARY = "com.example.app"
_AUTH = "com.example.auth"


def _eff(package: str, app_path: str | None = None) -> Effective:
    return Effective(
        target=package,
        platform_config=AndroidConfig(package=package, app_path=app_path),
        backend=["android"],
        device="booted",
        locale="en_US",
        launch_env={"FROM_CONFIG": "1"},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


class _Adb:
    """A scripted `adb` that records every argv and answers the queries a launch makes."""

    def __init__(self, *, running: set[str] | None = None, install_error: str = "") -> None:
        self.calls: list[list[str]] = []
        self.running = running or set()
        self.install_error = install_error

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "sys.boot_completed" in args:
            return "1\n"
        if "resolve-activity" in args:
            return f"{args[-1]}/.Main\n"
        if "pidof" in args:
            if args[-1] in self.running:
                return "1234\n"
            raise subprocess.CalledProcessError(1, args)
        if "install" in args and self.install_error:
            raise subprocess.CalledProcessError(1, args, output=self.install_error)
        return ""

    def ran(self, *words: str) -> bool:
        return any(all(w in argv for w in words) for argv in self.calls)


def _el(identifier: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": identifier,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


def _env(run: _Adb) -> AndroidEnvironment:
    return AndroidEnvironment("adb", "emulator-5554", adb_run=run)


# --- the member lifecycle ------------------------------------------------------------------------


def test_an_overwrite_member_installs_over_its_app_without_clearing_it(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "auth.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = _env(run)
    primary = env.start(_eff(_PRIMARY), Preconditions())
    run.calls.clear()
    member = env.start_member(_eff(_AUTH, str(apk)), Preconditions(reinstall="overwrite"))
    assert run.ran("install", str(apk))
    # Keep-data install: the member's own data survives, so nothing is cleared or uninstalled.
    assert not run.ran("pm", "clear", _AUTH)
    assert not run.ran("uninstall", _AUTH)
    assert run.ran("am", "start", f"{_AUTH}/.Main")
    assert isinstance(member, AdbDriver) and isinstance(primary, AdbDriver)
    assert member._front_check and primary._front_check  # both refuse other trees


def test_a_clean_member_reinstalls_and_clears_its_own_app(tmp_path: Path) -> None:
    apk = tmp_path / "auth.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions())
    env.start_member(_eff(_AUTH, str(apk)), Preconditions(reinstall="clean"))
    assert run.ran("uninstall", _AUTH)
    assert run.ran("pm", "clear", _AUTH)


def test_a_later_member_starts_without_reinstalling(tmp_path: Path) -> None:
    apk = tmp_path / "auth.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions())
    run.calls.clear()
    env.start_member(_eff(_AUTH, str(apk)), Preconditions(reinstall="clean"), install=False)
    assert not run.ran("install", str(apk))
    assert not run.ran("uninstall", _AUTH)
    assert run.ran("am", "start", f"{_AUTH}/.Main")


def test_install_app_stops_the_app_and_installs_over_it(tmp_path: Path) -> None:
    apk = tmp_path / "new.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    _env(run).install_member(_eff(_PRIMARY, str(apk)), keep_data=True)
    assert run.ran("force-stop", _PRIMARY)
    assert run.ran("install", str(apk))
    assert not run.ran("uninstall", _PRIMARY)
    run.calls.clear()
    _env(run).install_member(_eff(_PRIMARY, str(apk)), keep_data=False)
    assert run.ran("uninstall", _PRIMARY)


def test_a_downgrade_that_keeps_data_is_named(tmp_path: Path) -> None:
    apk = tmp_path / "old.apk"
    apk.write_bytes(b"apk")
    run = _Adb(install_error="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]")
    with pytest.raises(adb.DeviceError, match="use keepData: false for a downgrade"):
        _env(run).install_member(_eff(_PRIMARY, str(apk)), keep_data=True)


def test_install_app_refuses_a_missing_build(tmp_path: Path) -> None:
    with pytest.raises(adb.DeviceError, match="appPath not found"):
        _env(_Adb()).install_member(_eff(_PRIMARY, str(tmp_path / "gone.apk")), keep_data=True)


def test_ending_a_member_stops_its_app() -> None:
    run = _Adb()
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions())
    env.end_member(AdbDriver("emulator-5554", run=run), _eff(_AUTH))
    assert run.ran("force-stop", _AUTH)


# --- foreground ----------------------------------------------------------------------------------


def test_foreground_resumes_a_running_app_without_its_launch_env() -> None:
    run = _Adb(running={_AUTH})
    env = _env(run)
    control = env.controller(_eff(_AUTH))
    assert control is not None
    control.foreground()
    launch = next(argv for argv in run.calls if "start" in argv and "am" in argv)
    assert "FROM_CONFIG" not in launch


def test_foreground_launches_an_app_that_is_not_running_with_its_launch_env() -> None:
    run = _Adb()
    env = _env(run)
    control = env.controller(_eff(_AUTH))
    assert control is not None
    control.foreground()
    launch = next(argv for argv in run.calls if "start" in argv and "am" in argv)
    assert "FROM_CONFIG" in launch


def test_foreground_relaunches_the_primary_like_relaunch_without_terminating() -> None:
    run = _Adb()
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions())
    # A screen that is ready at once, so the readiness wait the launch ends with returns at once.
    env._drivers[_PRIMARY] = FakeDriver([_el("home"), _el("ok")])
    run.calls.clear()
    control = env.controller(_eff(_PRIMARY))
    assert control is not None
    control.foreground()
    assert not run.ran("force-stop", _PRIMARY)
    assert run.ran("am", "start", f"{_PRIMARY}/.Main")


# --- the front-app check -------------------------------------------------------------------------


def _screen(package: str, rows: int = 1) -> str:
    nodes = "".join(
        f'<node index="{i}" class="android.widget.Button" package="{package}" '
        f'resource-id="ok{i or ""}" text="OK" bounds="[0,{i * 10}][10,{i * 10 + 10}]" />'
        for i in range(rows)
    )
    return f"<?xml version='1.0' ?><hierarchy rotation=\"0\">{nodes}</hierarchy>"


def _driver(showing: str) -> AdbDriver:
    return AdbDriver(
        "emulator-5554",
        run=lambda args: "",
        fetch_hierarchy=lambda _since: HierarchyRead(_screen(showing)),
        package=_AUTH,
    )


def test_a_lone_driver_reads_whatever_is_in_front() -> None:
    assert len(_driver(_PRIMARY).query()) == 1


def test_a_member_driver_refuses_another_apps_tree_by_name() -> None:
    driver = _driver(_PRIMARY)
    driver.require_front_app()
    with pytest.raises(base.AppNotInFront, match=rf"{_AUTH} is not in front \({_PRIMARY} is\)"):
        driver.query()


def test_a_member_driver_reads_its_own_tree() -> None:
    driver = _driver(_AUTH)
    driver.require_front_app()
    assert len(driver.query()) == 1


def test_an_action_against_another_apps_tree_fails_by_name() -> None:
    driver = _driver(_PRIMARY)
    driver.require_front_app()
    with pytest.raises(base.AppNotInFront):
        driver.tap(base.Selector(id="ok"))


def test_adb_advertises_foreground_and_device_groups() -> None:
    caps = AdbDriver("emulator-5554", run=lambda a: "").capabilities()
    assert base.Capability.DC_FOREGROUND in caps
    assert base.Capability.DEVICE_GROUP in caps


def test_readiness_waits_through_another_apps_tree() -> None:
    # A member's `foreground` waits until its own tree is on screen: a read that still shows the
    # previous app is a transient startup state, not a failure (BE-0447).
    from bajutsu.common.platform_lifecycle import readiness

    shown: list[str] = []

    def fetch(_since: float | None) -> HierarchyRead:
        shown.append(_PRIMARY if not shown else _AUTH)
        return HierarchyRead(_screen(shown[-1], rows=2))

    driver = AdbDriver("emulator-5554", run=lambda args: "", fetch_hierarchy=fetch, package=_AUTH)
    driver.require_front_app()
    result = readiness.await_ready(driver, timeout=2.0, poll_init=0.0, poll_max=0.0)
    assert result.ready


def test_a_gestures_reply_showing_another_app_is_not_seeded() -> None:
    # A member's tap that opened another app carries that app's tree back; adopting it would let
    # a negative check read the other app's screen, so the next read runs and raises instead.
    driver = _driver(_AUTH)
    driver.require_front_app()
    driver._seed_from_act(HierarchyRead(_screen(_PRIMARY, rows=2), mark=2.0), 1.0)
    assert driver._seeded_tree is None
    driver._seed_from_act(HierarchyRead(_screen(_AUTH, rows=2), mark=2.0), 1.0)
    assert driver._seeded_tree is not None


def test_an_erase_clears_a_starting_member_too(tmp_path: Path) -> None:
    # Android has no device-wide wipe, so each member's own package is cleared (BE-0447).
    apk = tmp_path / "auth.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions(erase=True, reinstall="overwrite"))
    env.start_member(_eff(_AUTH, str(apk)), Preconditions(erase=True, reinstall="overwrite"))
    assert run.ran("uninstall", _AUTH)
    assert run.ran("pm", "clear", _AUTH)
    assert run.ran("force-stop", _AUTH)


def test_a_foreground_that_never_reaches_the_front_fails_its_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The readiness wait's own timeout, without spending it: the wait reports "not ready".
    monkeypatch.setattr(
        "bajutsu.common.platform_lifecycle.readiness.await_ready",
        lambda *a, **k: ReadinessResult(False, "timeout", 10.0, settled=False),
    )
    run = _Adb(running={_AUTH})
    env = _env(run)
    env.start(_eff(_PRIMARY), Preconditions())
    stuck = AdbDriver(
        "emulator-5554",
        run=lambda args: "",
        fetch_hierarchy=lambda _since: HierarchyRead(_screen(_PRIMARY, rows=2)),
        package=_AUTH,
    )
    stuck.require_front_app()
    env._drivers[_AUTH] = stuck
    control = env.controller(replace(_eff(_AUTH), ready_when=base.Selector(id="ok")))
    assert control is not None
    with pytest.raises(base.AppNotInFront, match="did not come to the front"):
        control.foreground()


def test_ending_a_member_that_shares_the_primarys_package_keeps_the_primarys_driver(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "new.apk"
    apk.write_bytes(b"apk")
    env = _env(_Adb())
    primary = env.start(_eff(_PRIMARY), Preconditions())
    member = env.start_member(_eff(_PRIMARY, str(apk)), Preconditions(), install=False)
    env.end_member(primary, _eff(_PRIMARY))  # not the member's driver: nothing leaves the map
    assert env._drivers[_PRIMARY] is member
    env.end_member(member, _eff(_PRIMARY))
    assert _PRIMARY not in env._drivers


def test_a_same_package_member_leaves_the_displaced_driver_checked(tmp_path: Path) -> None:
    # An update journey's old and new builds share a package; the old lease still reads through the
    # driver the new one displaced from the map, so it takes the front check too.
    env = _env(_Adb())
    old = env.start(_eff(_PRIMARY), Preconditions())
    env.start_member(_eff(_PRIMARY), Preconditions(), install=False)
    env.start_member(_eff(_AUTH), Preconditions(), install=False)
    assert isinstance(old, AdbDriver)
    assert old._front_check


def test_a_tree_naming_no_package_is_not_another_app() -> None:
    # The mid-transition empty dump names no package at all: "cannot tell", left to the retry.
    driver = AdbDriver(
        "emulator-5554",
        run=lambda args: "",
        fetch_hierarchy=lambda _since: HierarchyRead('<hierarchy rotation="0"/>'),
        package=_AUTH,
    )
    driver.require_front_app()
    assert driver._other_app_in_front(slice_root("<hierarchy/>")) is None


def test_another_apps_tree_is_never_read_as_untappable() -> None:
    driver = _driver(_PRIMARY)
    driver.require_front_app()
    with pytest.raises(base.AppNotInFront):
        driver.is_tappable(base.Selector(id="ok"))


def test_a_fresh_install_re_grants_the_configs_permissions(tmp_path: Path) -> None:
    apk = tmp_path / "new.apk"
    apk.write_bytes(b"apk")
    run = _Adb()
    eff = _eff(_PRIMARY, str(apk))
    eff = replace(
        eff,
        platform_config=AndroidConfig(
            package=_PRIMARY, app_path=str(apk), grant_permissions=["android.permission.CAMERA"]
        ),
    )
    _env(run).install_member(eff, keep_data=False)
    assert run.ran("pm", "grant", _PRIMARY, "android.permission.CAMERA")
