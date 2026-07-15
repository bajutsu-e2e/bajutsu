"""The `DeviceControl` factories for the device platforms (iOS via simctl, Android via adb).

These wrap a `simctl.Env` / `adb.Env` handle in the `DeviceControl` protocol the runner drives; they
share nothing with the environment classes beyond that handle, so they live beside each other here
rather than in the per-platform environment modules.
"""

from __future__ import annotations

from bajutsu import adb, simctl
from bajutsu.drivers import base
from bajutsu.orchestrator import DeviceControl


def device_control(
    udid: str, bundle_id: str, env_run: simctl.RunFn = simctl._real_run
) -> DeviceControl:
    """A `DeviceControl` bound to one device.

    Backs the `setLocation` / `push` / `clearKeychain` / `clearClipboard` / `setClipboard` /
    `background` / `foreground` / `overrideStatusBar` / `clearStatusBar` steps and the `clipboard`
    assertion (read-back) via simctl.

    Args:
        udid: The target device.
        bundle_id: The app the control acts on (e.g. for `push` / `foreground`).
        env_run: The subprocess runner for simctl, injectable for tests.
    """
    e = simctl.Env(udid, run=env_run)

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
            e.foreground(bundle_id)

        def override_status_bar(self, **kwargs: str | int) -> None:
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()


def android_device_control(
    serial: str, package: str, env_run: adb.RunFn = adb._real_run
) -> DeviceControl:
    """A `DeviceControl` for the Android emulator, backing only the operations it can honor.

    `setLocation` (`emu geo fix`) runs over the emulator console; the clipboard operations run over
    an ordered `am broadcast` to the app's in-app receiver (BajutsuAndroid, BE-0233) — hence `package`,
    to address the broadcast at the app under test. `push` / `clearKeychain` / the status-bar overrides
    / the app-lifecycle steps have no faithful emulator equivalent and raise `UnsupportedAction`.
    Preflight (BE-0212) rejects those steps up front from the adb backend's advertised subset, so this
    raise is the runtime backstop, never a silent no-op.

    Args:
        serial: The target emulator/device serial.
        package: The app under test's package, addressed by the clipboard broadcast.
        env_run: The subprocess runner for adb, injectable for tests.
    """
    e = adb.Env(serial, run=env_run)

    def _unsupported(op: str) -> base.UnsupportedAction:
        return base.UnsupportedAction(f"{op} is not supported on the Android emulator")

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def set_clipboard(self, text: str) -> None:
            e.set_clipboard(package, text)

        def get_clipboard(self) -> str:
            return e.get_clipboard(package)

        def clear_clipboard(self) -> None:
            e.clear_clipboard(package)

        def push(self, payload: dict[str, object]) -> None:
            raise _unsupported("push")

        def clear_keychain(self) -> None:
            raise _unsupported("clearKeychain")

        def home(self) -> None:
            raise _unsupported("background")

        def foreground(self) -> None:
            raise _unsupported("foreground")

        def override_status_bar(self, **kwargs: str | int) -> None:
            raise _unsupported("overrideStatusBar")

        def clear_status_bar(self) -> None:
            raise _unsupported("clearStatusBar")

    return _Control()
