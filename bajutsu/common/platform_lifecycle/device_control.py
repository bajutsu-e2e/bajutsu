"""The `DeviceControl` factories for the device platforms (iOS via simctl, Android via adb).

These wrap a `simctl.Env` / `adb.Env` handle in the `DeviceControl` protocol the runner drives; they
share nothing with the environment classes beyond that handle, so they live beside each other here
rather than in the per-platform environment modules.
"""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.common.backend_cli import adb, simctl
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator import DeviceControl


def device_control(
    udid: str,
    bundle_id: str,
    env_run: simctl.RunFn = simctl.real_run,
    *,
    foreground: Callable[[], None] | None = None,
) -> DeviceControl:
    """A `DeviceControl` bound to one device.

    Backs the `setLocation` / `push` / `clearKeychain` / `clearClipboard` / `setClipboard` /
    `background` / `foreground` / `overrideStatusBar` / `clearStatusBar` steps and the `clipboard`
    assertion (read-back) via simctl.

    Args:
        udid: The target device.
        bundle_id: The app the control acts on (e.g. for `push` / `foreground`).
        env_run: The subprocess runner for simctl, injectable for tests.
        foreground: The environment's own `foreground` (BE-0447), which launches the app when it
            is not running; None resumes it with `simctl launch`, as before.
    """
    e = simctl.Env(udid, run=env_run)
    bring_forward = foreground or (lambda: e.foreground(bundle_id))

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def push(self, payload: dict[str, object]) -> None:
            e.push(bundle_id, payload)

        def clear_keychain(self) -> None:
            e.clear_keychain()

        def clear_clipboard(self) -> None:
            e.clear_clipboard()

        def set_clipboard(self, text: str) -> None:
            e.set_clipboard(text)

        def get_clipboard(self) -> str:
            return e.get_clipboard()

        def home(self) -> None:
            e.home()

        def foreground(self) -> None:
            bring_forward()

        def override_status_bar(self, **kwargs: str | int) -> None:
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()


def android_device_control(
    serial: str,
    package: str,
    env_run: adb.RunFn = adb.real_run,
    *,
    foreground: Callable[[], None] | None = None,
    background: Callable[[], None] | None = None,
    record_mock_location: Callable[[float, float], None] | None = None,
) -> DeviceControl:
    """A `DeviceControl` for an Android emulator or device, backing only the operations it can honor.

    `setLocation` takes one of two routes, chosen at its first call and cached for the control's
    lease (BE-0481): a local emulator keeps the console's `emu geo fix`; every other target — a real
    device, or an emulator reached with `adb connect` — mocks the location through the app's in-app
    BajutsuAndroid receiver. The clipboard operations also run over an ordered `am broadcast` to the
    app (BE-0233) — hence `package`, to address both broadcasts at the app under test. `background`
    presses Home and the status-bar pair drives the System UI demo mode (BE-0478). `push` needs a
    receiver in the app's process, and Android has no keychain for `clearKeychain` to clear, so both
    raise `UnsupportedAction`. Preflight (BE-0212) rejects those steps up front from the adb backend's
    advertised subset, so this raise is the runtime backstop, never a silent no-op.

    Args:
        serial: The target emulator/device serial.
        package: The app under test's package, addressed by the in-app broadcasts.
        env_run: The subprocess runner for adb, injectable for tests.
        foreground: The environment's own `foreground` (BE-0447), which launches the app when it
            is not running and waits for it; None leaves `foreground` unsupported.
        background: The environment's own `background`, which presses Home and waits for the app
            to leave the front (BE-0478); None presses Home without waiting.
        record_mock_location: Told the coordinates before the in-app route grants the mock-location
            app-op, so the environment can re-send them after a relaunch and undo them at teardown
            even when the broadcast that follows fails; None records nothing.
    """
    e = adb.Env(serial, run=env_run)

    def _unsupported(op: str) -> base.UnsupportedAction:
        return base.UnsupportedAction(f"{op} is not supported on Android")

    bring_forward = _android_foreground(foreground)
    send_home = background or e.home
    set_location = _AndroidLocation(e, package, record_mock_location)

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            set_location(lat, lon)

        def set_clipboard(self, text: str) -> None:
            e.set_clipboard(package, text)

        def get_clipboard(self) -> str:
            return e.get_clipboard(package)

        def clear_clipboard(self) -> None:
            e.clear_clipboard(package)

        def push(self, payload: dict[str, object]) -> None:  # noqa: ARG002  # DeviceControl shape
            raise _unsupported("push")

        def clear_keychain(self) -> None:
            raise _unsupported("clearKeychain")

        def home(self) -> None:
            send_home()

        def foreground(self) -> None:
            bring_forward()

        def override_status_bar(self, **kwargs: str | int) -> None:
            _require_showable_status_bar(kwargs)
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()


class _AndroidLocation:
    """`setLocation`'s route on Android, probed at the first call and kept for the lease (BE-0481)."""

    def __init__(
        self, e: adb.Env, package: str, record: Callable[[float, float], None] | None
    ) -> None:
        self._e = e
        self._package = package
        self._record = record
        # The target does not change kind within a lease, so the probe's answer is read once.
        self._console: bool | None = None

    def __call__(self, lat: float, lon: float) -> None:
        if self._console is None:
            self._console = self._e.has_emulator_console()
        if self._console:
            self._e.set_location(lat, lon)
            return
        if self._record is not None:
            self._record(lat, lon)
        self._e.set_mock_location(self._package, lat, lon)


def _require_showable_status_bar(fields: dict[str, str | int]) -> None:
    """The backstop to the Android preflight check (BE-0478).

    `am broadcast` reports success for a value System UI then ignores, so the step fails here rather
    than passing with the status bar unpinned.
    """
    if problems := adb.status_bar_problems(fields):
        raise base.UnsupportedAction(f"overrideStatusBar: {'; '.join(problems)}")


def _android_foreground(foreground: Callable[[], None] | None) -> Callable[[], None]:
    """The Android control's `foreground`: the environment's own, else an `UnsupportedAction`."""
    if foreground is not None:
        return foreground

    def unsupported() -> None:
        raise base.UnsupportedAction(
            "foreground is not supported on Android without its environment"
        )

    return unsupported
