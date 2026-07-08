"""adb wrapper — clean-state / launch / deeplink / input / screencap / device list.

The Android environment ([BE-0007]) is the twin of the iOS `simctl` sequence: a clean state is
`pm clear <package>` (the `erase` equivalent), launch is `am start`, and a deeplink is an
`am start -a android.intent.action.VIEW`. Command builders are pure and unit-tested; execution
goes through an injectable runner so the device-touching part stays thin and swappable in tests —
the same shape as `simctl.py`.

adb carries everything an operation needs in its argv (intent extras included), so the runner is
the plain ``argv -> stdout`` form the idb driver uses, not simctl's ``(argv, env)`` — no launch env
is forwarded through the parent process.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping

from bajutsu import simctl

# argv -> stdout. adb needs no parent-process env (unlike simctl's SIMCTL_CHILD_*).
RunFn = Callable[[list[str]], str]


class DeviceError(simctl.DeviceError):
    """An adb operation failed in a way the user can act on (e.g. no emulator, app not installed).

    Carries a clean, actionable message — the CLI surfaces it and exits 2, the same boundary as the
    iOS device errors. It subclasses `simctl.DeviceError` so the CLI entrypoints that already catch
    that type (`run` / `crawl` / `audit` / `record`) surface an Android device failure the same way,
    as a clean exit-2 rather than an unhandled traceback.
    """


def device_error(exc: subprocess.CalledProcessError) -> DeviceError:
    """Turn a raw adb failure into a clean DeviceError, keeping the command, exit code, and stderr."""
    cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(map(str, exc.cmd or []))
    # stderr is str under our text=True runs, but decode bytes too (a text=False caller) so the most
    # actionable part of the failure is never silently dropped.
    stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
    detail = ((stderr if isinstance(stderr, str) else "") or "").strip()
    msg = f"device operation failed (exit {exc.returncode}): {cmd}"
    return DeviceError(f"{msg}\n{detail}" if detail else msg)


def _real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _num(v: float) -> str:
    return str(round(v))  # `input tap`/`swipe` take integer coordinates


# A device serial / emulator id: alphanumerics plus `. _ : -`, never leading with `-` (which adb
# would read as an option). Every command builder validates the serial through `_adb`, so an id
# from `--udid` / config can neither inject an adb option nor reach a subprocess argv unchecked.
_SERIAL = re.compile(r"[A-Za-z0-9._:][A-Za-z0-9._:-]*")


def _checked_serial(serial: str) -> str:
    if not _SERIAL.fullmatch(serial):
        raise DeviceError(f"invalid device serial: {serial!r}")
    return serial


def _adb(serial: str, *rest: str) -> list[str]:
    return ["adb", "-s", _checked_serial(serial), *rest]


# --- command builders ---


def devices_cmd() -> list[str]:
    """List attached devices/emulators (`adb devices`), one `<serial>\\t<state>` per line."""
    return ["adb", "devices"]


def get_prop_cmd(serial: str, prop: str) -> list[str]:
    return _adb(serial, "shell", "getprop", prop)


def dump_cmd(serial: str) -> list[str]:
    """Stream the current window's UI Automator hierarchy XML to stdout (no on-device temp file)."""
    return _adb(serial, "exec-out", "uiautomator", "dump", "/dev/tty")


def screencap_cmd(serial: str) -> list[str]:
    """Capture the screen as PNG bytes on stdout (`exec-out` keeps the stream binary-clean)."""
    return _adb(serial, "exec-out", "screencap", "-p")


def tap_cmd(serial: str, x: float, y: float) -> list[str]:
    return _adb(serial, "shell", "input", "tap", _num(x), _num(y))


def swipe_cmd(serial: str, x1: float, y1: float, x2: float, y2: float, ms: int = 300) -> list[str]:
    # A finite duration makes it a real drag; a zero-duration swipe is a fling, not a pan.
    return _adb(serial, "shell", "input", "swipe", _num(x1), _num(y1), _num(x2), _num(y2), str(ms))


def shell_cmd(serial: str) -> list[str]:
    """An `adb shell` with no command — the device command is fed on stdin (see `text_script`)."""
    return _adb(serial, "shell")


def text_script(text: str) -> str:
    """The device-side `input text` command line, safe to feed to `adb shell` over stdin.

    Fed on stdin — not as an `adb` argv token — so a secret / OTP typed by a scenario never appears
    in the host process's command line where `ps` could read it (BE-0155 parity with idb, which
    passes typed text to `idb ui text` on stdin for the same reason). Spaces become `input`'s `%s`
    escape (it splits its argument on spaces), and the result is single-quoted for the device shell.
    """
    return f"input text {shlex.quote(text.replace(' ', '%s'))}"


def pm_clear_cmd(serial: str, package: str) -> list[str]:
    """Reset the app's data/state — the Android `erase` equivalent."""
    return _adb(serial, "shell", "pm", "clear", package)


def force_stop_cmd(serial: str, package: str) -> list[str]:
    return _adb(serial, "shell", "am", "force-stop", package)


def install_cmd(serial: str, apk_path: str) -> list[str]:
    # -r reinstall keeping data, -t allow test/debug APKs (the showcase builds are debug).
    return _adb(serial, "install", "-r", "-t", apk_path)


def resolve_activity_cmd(serial: str, package: str) -> list[str]:
    """Ask the package manager for the launcher component, so a launch needs no configured activity.

    `--brief` prints `<package>/<activity>` on the last line; parsed by `Env.resolve_activity`.
    """
    return _adb(serial, "shell", "cmd", "package", "resolve-activity", "--brief", package)


def launch_cmd(serial: str, component: str, extras: Mapping[str, str] | None = None) -> list[str]:
    """Launch `component` (`<package>/<activity>`), forwarding launch env as intent extras.

    `-W` waits for the launch to complete (a bounded wait the platform owns, not a fixed sleep);
    each launchEnv key/value becomes a string extra the launcher Activity reads once (SPEC §3).
    """
    cmd = _adb(serial, "shell", "am", "start", "-W", "-n", component)
    for key, val in (extras or {}).items():
        cmd += ["--es", key, val]
    return cmd


def deeplink_cmd(serial: str, url: str, package: str) -> list[str]:
    """Open a deeplink, scoped to `package` so the intent resolves to the app under test."""
    return _adb(
        serial, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, package
    )


# --- device catalog / serial resolution ---


def _parse_devices(text: str) -> list[str]:
    """Serials in the `device` state from `adb devices` output (offline/unauthorized excluded)."""
    serials: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def booted_serials(run: RunFn = _real_run) -> list[str]:
    """Serials of the currently-attached, ready devices/emulators (empty on any failure)."""
    try:
        return _parse_devices(run(devices_cmd()))
    except (subprocess.CalledProcessError, OSError):
        return []


def resolve_serial(serial: str, run: RunFn = _real_run) -> str:
    """Resolve the alias "booted" to a concrete serial (the first ready device).

    A concrete serial passes through unchanged; "booted" picks the single ready device (the first
    if several). Falls back to "booted" if resolution fails, so the caller fails loudly downstream
    rather than silently targeting the wrong device.
    """
    if serial != "booted":
        return serial
    found = booted_serials(run)
    return found[0] if found else serial


def device_catalog(run: RunFn = _real_run) -> dict[str, dict[str, str]]:
    """Map serial -> {'name', 'runtime'} for the attached devices (best-effort, {} on any failure).

    Lets a run label which emulator (device model + Android version) each scenario ran on, the
    Android peer of `simctl.device_catalog`.
    """
    catalog: dict[str, dict[str, str]] = {}
    for serial in booted_serials(run):
        try:
            model = run(get_prop_cmd(serial, "ro.product.model")).strip()
            release = run(get_prop_cmd(serial, "ro.build.version.release")).strip()
        except (subprocess.CalledProcessError, OSError):
            continue
        catalog[serial] = {"name": model, "runtime": f"Android {release}" if release else "Android"}
    return catalog


class Env:
    """Thin adb front end for one device/emulator."""

    def __init__(self, serial: str, run: RunFn = _real_run) -> None:
        self.serial = serial
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

    def force_stop(self, package: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(force_stop_cmd(self.serial, package))

    def resolve_activity(self, package: str) -> str:
        """The launcher component (`<package>/<activity>`) for `package`, via the package manager.

        Raises:
            DeviceError: the package manager returned no launcher activity (app not installed, or no
                launcher intent) — surfaced cleanly rather than launching an empty component.
        """
        out = self._run(resolve_activity_cmd(self.serial, package))
        for line in reversed(out.splitlines()):
            line = line.strip()
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
        with open(path, "wb") as f:
            f.write(out)
