"""The thin adb front end for one device or emulator."""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from ._device_state import (
    APP_LOCALES_MIN_API,
    get_app_locales_cmd,
    home_cmd,
    language_tag,
    parse_app_locales,
    resumed_activities_cmd,
    resumed_packages,
    set_app_locales_cmd,
    status_bar_demo_cmds,
    status_bar_exit_cmd,
)
from ._functions import (
    SEED_PHOTOS_CLEARED,
    SEED_PHOTOS_DIR,
    checked_serial,
    clear_primary_clip_cmd,
    deeplink_cmd,
    force_stop_cmd,
    geo_fix_cmd,
    get_primary_clip_cmd,
    get_prop_cmd,
    install_cmd,
    launch_cmd,
    media_scan_cmd,
    parse_clipboard_result,
    pm_clear_cmd,
    pm_grant_cmd,
    pm_revoke_cmd,
    push_cmd,
    real_run,
    resolve_activity_cmd,
    screencap_cmd,
    seed_photo_name,
    seed_photo_time_cmd,
    seed_photos_clear_cmd,
    seed_photos_reset_cmd,
    set_primary_clip_cmd,
    uninstall_cmd,
)
from ._shared import RunFn
from .device_error import DeviceError

# The permission-vocabulary service (BE-0276, shared with iOS's TCC map in simctl.py) -> the
# android.permission.* names it grants/revokes. A service maps to more than one permission when
# Android splits it (fine + coarse location; read + write contacts/calendar); `pm grant`/`pm
# revoke` runs once per mapped permission. Covers the whole vocabulary — the adb backend advertises
# every service, unlike iOS's `notifications` gap — so `Env.apply_permissions` never misses a key
# for a service preflight already admitted.
SERVICE_TO_ANDROID_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "location": (
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
    ),
    "camera": ("android.permission.CAMERA",),
    "microphone": ("android.permission.RECORD_AUDIO",),
    "contacts": ("android.permission.READ_CONTACTS", "android.permission.WRITE_CONTACTS"),
    # `photos` requires API 33+ (`READ_MEDIA_IMAGES`/`READ_MEDIA_VIDEO`); a target below API 33 has
    # no mapping here and would need the legacy `READ_EXTERNAL_STORAGE` permission instead.
    "photos": ("android.permission.READ_MEDIA_IMAGES", "android.permission.READ_MEDIA_VIDEO"),
    "calendar": ("android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR"),
    "notifications": ("android.permission.POST_NOTIFICATIONS",),
}


class Env:
    """Thin adb front end for one device/emulator."""

    def __init__(self, serial: str, run: RunFn = real_run) -> None:
        # Validate at construction (like AdbDriver): Env is what AndroidEnvironment.start drives
        # for the real device-lifecycle path, so a bad serial fails here, not deep in a command.
        self.serial = checked_serial(serial)
        self._run = run

    def boot_completed(self) -> bool:
        """Whether `sys.boot_completed` is `1` — the boot-readiness signal polled as a condition
        wait (no fixed sleep), the Android peer of `simctl bootstatus`.

        A device adb cannot see yet reads as "not booted" (retried by the poll), but a missing `adb`
        binary is not a transient not-booted-yet state, so `FileNotFoundError` propagates rather than
        being masked into a spin to the boot deadline.
        """
        try:
            return self._run(get_prop_cmd(self.serial, "sys.boot_completed")).strip() == "1"
        except subprocess.CalledProcessError:
            return False  # adb ran but the device is not ready yet
        except FileNotFoundError:
            raise  # adb itself is absent — fail fast, do not spin
        except OSError:
            return False  # a transient runner error; the next poll retries

    def clear(self, package: str) -> None:
        self._run(pm_clear_cmd(self.serial, package))

    def install(self, apk_path: str) -> None:
        self._run(install_cmd(self.serial, apk_path))

    def uninstall(self, package: str) -> None:
        """Remove `package` if it is there, so the install that follows describes the APK alone.

        A leftover install is not neutral: `install -r` refuses one whose signature differs, and where
        it succeeds it keeps components the new build renamed or dropped, leaving the device running a
        mix of two builds. Absent is the ordinary case on a fresh emulator, and `adb uninstall` fails
        for it, so the failure is suppressed the way `force_stop` suppresses its own.
        """
        with contextlib.suppress(subprocess.CalledProcessError, OSError):
            self._run(uninstall_cmd(self.serial, package))

    def add_media(self, media_paths: Sequence[str]) -> None:
        """Make the seed directory hold exactly *media_paths*, in order, and rescan it.

        An empty list still empties a directory an earlier seed left behind, so a scenario that seeds
        nothing never sees another scenario's images; a device never seeded is left untouched. Each
        image gets a fixed, ascending modification time, so a picker that sorts by date shows the
        list's last entry first, as the iOS `simctl addmedia` order does — for images without an EXIF
        capture date, which the media store prefers. The scan is asynchronous: a scenario waits for
        a seeded image to appear before selecting it.
        """
        if not media_paths:
            if SEED_PHOTOS_CLEARED in self._run(seed_photos_clear_cmd(self.serial)):
                self._run(media_scan_cmd(self.serial))
            return
        # Every name first, so a bad path fails before the directory is emptied.
        names = [seed_photo_name(index, path) for index, path in enumerate(media_paths)]
        self._run(seed_photos_reset_cmd(self.serial))
        for index, (path, name) in enumerate(zip(media_paths, names, strict=True)):
            self._run(push_cmd(self.serial, path, f"{SEED_PHOTOS_DIR}/{name}"))
            self._run(seed_photo_time_cmd(self.serial, index, name))
        self._run(media_scan_cmd(self.serial))

    def force_stop(self, package: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(force_stop_cmd(self.serial, package))

    def _pm_run(self, action: str, package: str, permission: str) -> None:
        """Run one `pm grant`/`pm revoke` and surface any stdout as a `DeviceError`.

        `pm grant`/`pm revoke` exit 0 even for an unknown permission or an app that predates
        runtime permissions, printing the error to stdout — so a silent mistake would otherwise
        surface only as a later, misleading step failure. Any stdout (silent on success) is
        surfaced loudly instead. Shared by `grant_permissions` (the config-level list, BE-0210) and
        `apply_permissions` (the per-scenario field, BE-0276) — same command shape, same contract.

        Raises:
            DeviceError: `action` is neither `grant` nor `revoke` (should not happen — every caller
                passes a literal or an already-validated `Scenario.permissions` value — but this
                fails loudly rather than silently falling through to one command or the other), or
                `pm grant`/`pm revoke` reported a problem.
        """
        if action == "grant":
            cmd_for = pm_grant_cmd
        elif action == "revoke":
            cmd_for = pm_revoke_cmd
        else:
            raise DeviceError(f"unknown pm action: {action!r} (expected grant|revoke)")
        out = self._run(cmd_for(self.serial, package, permission)).strip()
        if out:
            raise DeviceError(f"pm {action} failed for {permission} on {package}: {out}")

    def grant_permissions(self, package: str, permissions: list[str]) -> None:
        """Grant each configured runtime permission up front (BE-0210), one `pm grant` per entry.

        Raises:
            DeviceError: see `_pm_run`.
        """
        for permission in permissions:
            self._pm_run("grant", package, permission)

    def apply_permissions(self, package: str, permissions: Mapping[str, str]) -> None:
        """Grant or revoke each `service: grant|revoke` entry in `permissions` up front (BE-0276),
        one `pm grant`/`pm revoke` per mapped `android.permission.*` — the per-scenario twin of
        `grant_permissions`'s config-level list.

        Every entry's service and action are validated before any `pm` call runs, so an unmapped
        service or an unrecognized action fails before the device is touched at all — never
        partway through, leaving some services already mutated (should not happen in practice —
        the adb backend advertises the whole vocabulary and `Scenario.permissions` validates the
        action, so preflight/schema would have already rejected it — but this validation is the
        runtime backstop for a caller that bypasses both).

        Raises:
            DeviceError: a service has no mapping, an action is neither `grant` nor `revoke`, or
                see `_pm_run`.
        """
        for service, action in permissions.items():
            if service not in SERVICE_TO_ANDROID_PERMISSIONS:
                raise DeviceError(f"permissions.{service} has no android.permission.* mapping")
            if action not in ("grant", "revoke"):
                raise DeviceError(f"unknown pm action: {action!r} (expected grant|revoke)")
        for service, action in permissions.items():
            for permission in SERVICE_TO_ANDROID_PERMISSIONS[service]:
                self._pm_run(action, package, permission)

    def resolve_activity(self, package: str) -> str:
        """The launcher component (`<package>/<activity>`) for `package`, via the package manager.

        Raises:
            DeviceError: the package manager returned no launcher activity (app not installed, or no
                launcher intent) — surfaced cleanly rather than launching an empty component.
        """
        out = self._run(resolve_activity_cmd(self.serial, package))
        for raw in reversed(out.splitlines()):
            line = raw.strip()
            # A launcher component is `<package>/<activity>`: a `/` with a non-empty left side and
            # no spaces. Requiring a non-empty left side rejects a stray absolute path (`/data/…`)
            # in the manager's chatter that would otherwise be launched as a bogus component.
            head, sep, tail = line.partition("/")
            if sep and head and tail and " " not in line:
                return line
        raise DeviceError(f"no launcher activity for {package} (is it installed?)")

    def launch(self, package: str, env: Mapping[str, str] | None = None) -> None:
        """Launch the app's default launcher activity, forwarding `env` as intent extras."""
        self._run(launch_cmd(self.serial, self.resolve_activity(package), env or {}))

    def open_url(self, url: str, package: str) -> None:
        self._run(deeplink_cmd(self.serial, url, package))

    def screenshot(self, path: str) -> None:
        """Write a PNG screenshot to `path` from `screencap`'s binary stdout.

        Routed through a class-level attribute (like `simctl.Env._run_pbcopy`) so tests can patch
        the binary capture without a device, and so the PNG bytes never pass through the text RunFn.
        """
        self._run_capture(screencap_cmd(self.serial), path)

    @staticmethod
    def _run_capture(cmd: list[str], path: str) -> None:
        out = subprocess.run(cmd, capture_output=True, check=True).stdout
        with Path(path).open("wb") as f:
            f.write(out)

    # Device control: the subset the emulator can honor, the Android peer of simctl's setLocation /
    # clipboard / Home / status bar. setLocation is a pure emulator-console op (BE-0211); clipboard goes
    # through the app's in-app receiver (BE-0233), so its methods take the target package to address the
    # broadcast; Home and the status bar need no app at all (BE-0478). `push` needs a receiver in the
    # app's process and Android has no keychain, so neither is wired (see
    # `platform_lifecycle.device_control.android_device_control`).

    def set_location(self, lat: float, lon: float) -> None:
        self._run(geo_fix_cmd(self.serial, lat, lon))

    def set_clipboard(self, package: str, text: str) -> None:
        parse_clipboard_result(self._run(set_primary_clip_cmd(self.serial, package, text)))

    def clear_clipboard(self, package: str) -> None:
        parse_clipboard_result(self._run(clear_primary_clip_cmd(self.serial, package)))

    def get_clipboard(self, package: str) -> str:
        return parse_clipboard_result(self._run(get_primary_clip_cmd(self.serial, package)))

    def home(self) -> None:
        """Press Home, sending the app to the background with its process alive (BE-0478)."""
        self._run(home_cmd(self.serial))

    def resumed_packages(self) -> set[str]:
        """The packages holding a resumed activity now, the signal `background` waits on."""
        return resumed_packages(self._run(resumed_activities_cmd(self.serial)))

    def override_status_bar(self, **kwargs: str | int) -> None:
        """Freeze the status bar through the System UI demo mode (`simctl status_bar`'s twin)."""
        for cmd in status_bar_demo_cmds(self.serial, kwargs):
            self._run(cmd)

    def clear_status_bar(self) -> None:
        self._run(status_bar_exit_cmd(self.serial))

    def apply_app_locale(self, package: str, locale: str | None, api_level: int | None) -> None:
        """Pin *package*'s per-app language to *locale*, or clear it when *locale* is None (BE-0478).

        A set value is read back, because `cmd locale` accepts a tag the system then ignores. An unset
        locale clears whatever an earlier run on this reused emulator left behind, so the app falls
        back to the device's own language; below API level 33 nothing could have set one.

        Raises:
            DeviceError: A locale is set but the API level is unknown or below 33, or the read-back
                does not match.
        """
        if locale is None:
            if api_level is not None and api_level >= APP_LOCALES_MIN_API:
                self._run(set_app_locales_cmd(self.serial, package, ""))
                self._confirm_app_locale(package, "")
            return
        if api_level is None or api_level < APP_LOCALES_MIN_API:
            level = "an unreadable API level" if api_level is None else f"API level {api_level}"
            raise DeviceError(
                f"runsOn.android.locale {locale!r} needs API level {APP_LOCALES_MIN_API} or later, "
                f"but the device reports {level}"
            )
        tag = language_tag(locale)
        self._run(set_app_locales_cmd(self.serial, package, tag))
        self._confirm_app_locale(package, tag)

    def _confirm_app_locale(self, package: str, tag: str) -> None:
        # `cmd locale` accepts a value the system then ignores, so the read-back is the proof.
        got = parse_app_locales(self._run(get_app_locales_cmd(self.serial, package)))
        if got != tag:
            action = f"setting {tag!r}" if tag else "clearing the one an earlier run left"
            raise DeviceError(
                f"runsOn.android.locale: {package} reports locale {got!r} after {action}"
            )
