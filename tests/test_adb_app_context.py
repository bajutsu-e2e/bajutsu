"""The adb backend's `app:` step (issue #2393): `enter_app` / `leave_app` over `am start`.

Entering starts the package's launcher activity and waits, on a condition, until that package holds
the resumed activity; leaving does the same for the app beneath it on the driver's own stack. No
device: a scripted `run` plays the package manager, the activity manager, and `pidof`.
"""

from __future__ import annotations

import subprocess

import pytest

import bajutsu.common.drivers.adb.adb_driver as adb_driver_impl
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver, HierarchyRead

_SUBJECT = "com.example.subject"
_SETTINGS = "com.android.settings"
_FILES = "com.android.documentsui"


def _activities(package: str | None) -> str:
    record = f"ActivityRecord{{1a2b u0 {package}/.Main t42}}" if package else "null"
    return f"ACTIVITY MANAGER ACTIVITIES\n  topResumedActivity={record}\n"


class _Device:
    """A scripted device whose resumed package follows every `am start` after `lag` reads."""

    def __init__(self, *, lag: int = 0, installed: tuple[str, ...] = (_SETTINGS, _FILES)) -> None:
        self.front: str | None = _SUBJECT
        self.installed = {_SUBJECT, *installed}
        self.lag = lag
        self.calls: list[list[str]] = []
        self._pending: str | None = None
        self._reads_left = 0

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if "resolve-activity" in args:
            package = args[-1]
            return (
                f"priority=0\n{package}/.Main\n"
                if package in self.installed
                else "No activity found\n"
            )
        if args[4:6] == ["am", "start"]:
            self._pending = args[args.index("-n") + 1].split("/")[0]
            self._reads_left = self.lag
            return "Status: ok\n"
        if args[-2:] == ["activity", "activities"]:
            if self._pending is not None:
                if self._reads_left == 0:
                    self.front, self._pending = self._pending, None
                else:
                    self._reads_left -= 1
            return _activities(self.front)
        if "pidof" in args:
            return "4242\n"  # the app under test keeps its process throughout
        raise AssertionError(f"unexpected adb call: {args}")

    def started(self) -> list[str]:
        return [a[a.index("-n") + 1] for a in self.calls if a[4:6] == ["am", "start"]]


def _driver(device: _Device, **kwargs: object) -> AdbDriver:
    return AdbDriver("emulator-5554", run=device.run, package=_SUBJECT, **kwargs)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tiny poll intervals keep the suite fast; the timeout stays generous so a success-path test
    # never runs out of time on a loaded CI host.
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_TIMEOUT", 10.0)
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_POLL", 0.001)
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_POLL_MAX", 0.001)


@pytest.fixture
def _short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only for the tests that expect the wait to run out.
    monkeypatch.setattr(adb_driver_impl, "_APP_FRONT_TIMEOUT", 0.3)


def test_adb_advertises_app_context() -> None:
    assert (
        base.Capability.APP_CONTEXT in AdbDriver("emulator-5554", run=lambda a: "").capabilities()
    )


def test_enter_starts_the_launcher_activity_and_waits_for_it_to_resume() -> None:
    device = _Device(lag=2)
    driver = _driver(device)
    driver.enter_app(_SETTINGS)
    assert device.started() == [f"{_SETTINGS}/.Main"]
    assert device.front == _SETTINGS
    # The wait polled until the package held the resumed activity, not a fixed pause.
    assert sum(a[-2:] == ["activity", "activities"] for a in device.calls) == 3


def test_enter_retires_the_settled_cache() -> None:
    # The screen changed under the driver without one of its own gestures, so a key or tree proven
    # before (or during) the wait must not survive it.
    device = _Device(lag=1)
    driver = _driver(device)
    driver._settled_key = object()  # type: ignore[assignment]
    driver._tree_current = True
    driver.enter_app(_SETTINGS)
    assert driver._settled_key is None
    assert driver._tree_current is False


def test_leave_brings_back_the_app_under_test() -> None:
    device = _Device()
    driver = _driver(device)
    driver.enter_app(_SETTINGS)
    driver.leave_app()
    assert device.started() == [f"{_SETTINGS}/.Main", f"{_SUBJECT}/.Main"]
    assert device.front == _SUBJECT


def test_nested_blocks_leave_to_the_app_beneath_not_past_it() -> None:
    device = _Device()
    driver = _driver(device)
    driver.enter_app(_SETTINGS)
    driver.enter_app(_FILES)
    driver.leave_app()
    assert device.front == _SETTINGS
    driver.leave_app()
    assert device.front == _SUBJECT
    assert device.started()[-2:] == [f"{_SETTINGS}/.Main", f"{_SUBJECT}/.Main"]


@pytest.mark.usefixtures("_short_timeout")
def test_an_app_that_never_reaches_the_front_fails_the_step() -> None:
    device = _Device(lag=10_000)
    driver = _driver(device)
    with pytest.raises(base.ElementNotFound, match="did not reach the foreground"):
        driver.enter_app(_SETTINGS)
    # A failed enter opens no block, so there is nothing for a leave to pop.
    with pytest.raises(base.UnsupportedAction):
        driver.leave_app()


def test_an_uninstalled_package_fails_before_any_launch() -> None:
    device = _Device(installed=())
    with pytest.raises(base.ElementNotFound, match="no launcher activity"):
        _driver(device).enter_app(_SETTINGS)
    assert device.started() == []


@pytest.mark.usefixtures("_short_timeout")
def test_an_unreadable_dump_is_ridden_out_then_named_if_the_wait_runs_out() -> None:
    device = _Device()
    real = device.run

    def flaky(args: list[str]) -> str:
        if args[-2:] == ["activity", "activities"]:
            device.calls.append(args)
            return "no such field\n"
        return real(args)

    with pytest.raises(base.ElementNotFound, match="last read failed"):
        AdbDriver("emulator-5554", run=flaky, package=_SUBJECT).enter_app(_SETTINGS)


def test_one_unreadable_dump_is_ridden_out() -> None:
    # The `continue` is the point of the except branch: one mid-transition dump is not an answer
    # either way, so the wait keeps polling rather than failing the step.
    device = _Device()
    real = device.run
    reads = 0

    def flaky(args: list[str]) -> str:
        nonlocal reads
        if args[-2:] == ["activity", "activities"]:
            reads += 1
            if reads == 1:
                device.calls.append(args)
                return "no such field\n"
        return real(args)

    AdbDriver("emulator-5554", run=flaky, package=_SUBJECT).enter_app(_SETTINGS)
    assert device.front == _SETTINGS


@pytest.mark.usefixtures("_short_timeout")
def test_a_failed_leave_still_closes_the_block() -> None:
    device = _Device()
    driver = _driver(device)
    driver.enter_app(_SETTINGS)
    device.lag = 10_000
    with pytest.raises(base.ElementNotFound):
        driver.leave_app()
    # Popped regardless, as the iOS runner does: the next leave has no block to close.
    with pytest.raises(base.UnsupportedAction):
        driver.leave_app()


def test_leave_without_a_package_to_return_to_is_refused() -> None:
    device = _Device()
    driver = AdbDriver("emulator-5554", run=device.run)
    driver.enter_app(_SETTINGS)
    with pytest.raises(base.UnsupportedAction, match="no app under test"):
        driver.leave_app()


def test_a_launch_failure_surfaces_as_a_device_error() -> None:
    from bajutsu.common.backend_cli import adb

    def broken(args: list[str]) -> str:
        if "resolve-activity" in args:
            return f"{_SETTINGS}/.Main\n"
        raise subprocess.CalledProcessError(1, args, stderr="device offline")

    with pytest.raises(adb.DeviceError, match="device offline"):
        AdbDriver("emulator-5554", run=broken, package=_SUBJECT).enter_app(_SETTINGS)


def test_the_crash_probe_does_not_read_the_handoff_as_a_crash() -> None:
    # The app under test keeps its process behind another app, so `pidof` still answers and the
    # probe has nothing to confirm (BE-0424).
    device = _Device()
    driver = _driver(device, api_level=34, launched_at=lambda: (0.0, "2026-01-01 00:00:00"))
    driver.enter_app(_SETTINGS)
    assert driver.app_crash_signal() is None


def _screen(package: str) -> str:
    return (
        "<?xml version='1.0' ?><hierarchy rotation=\"0\">"
        f'<node index="0" class="android.widget.Button" package="{package}" '
        'resource-id="ok" text="OK" bounds="[0,0][10,10]" /></hierarchy>'
    )


def test_the_front_check_follows_the_entered_app() -> None:
    # On a device a device group shares (BE-0447), a read must show the app the driver addresses:
    # inside an `app:` block that is the entered package, and the app under test again after it.
    device = _Device()
    driver = AdbDriver(
        "emulator-5554",
        run=device.run,
        fetch_hierarchy=lambda _since: HierarchyRead(_screen(device.front or "")),
        package=_SUBJECT,
    )
    driver.require_front_app()
    driver.enter_app(_SETTINGS)
    assert len(driver.query()) == 1
    device.front = _SUBJECT  # the entered app vanished from under the block
    with pytest.raises(base.AppNotInFront, match=rf"{_SETTINGS} is not in front"):
        driver.query()
    driver.leave_app()
    assert len(driver.query()) == 1
