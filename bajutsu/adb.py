"""adb wrapper — clean-state / launch / deeplink / input / screencap / device list.

The Android environment ([BE-0007]) is the twin of the iOS `simctl` sequence: a clean state is
`pm clear <package>` (the `erase` equivalent), launch is `am start`, and a deeplink is an
`am start -a android.intent.action.VIEW`. Command builders are pure and unit-tested; execution
goes through an injectable runner so the device-touching part stays thin and swappable in tests —
the same shape as `simctl.py`.

adb carries everything an operation needs in its argv (intent extras included), so the runner is
the plain ``argv -> stdout`` form, not simctl's ``(argv, env)`` — no launch env
is forwarded through the parent process.
"""

from __future__ import annotations

import base64
import contextlib
import math
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from bajutsu import device_errors
from bajutsu.device_id import is_valid_device_id

# argv -> stdout. adb needs no parent-process env (unlike simctl's SIMCTL_CHILD_*).
RunFn = Callable[[list[str]], str]


class DeviceError(device_errors.DeviceError):
    """An adb operation failed in a way the user can act on (e.g. no emulator, app not installed).

    Carries a clean, actionable message — the CLI surfaces it and exits 2, the same boundary as the
    iOS device errors. The Android-specific subclass of the platform-neutral
    `device_errors.DeviceError` (BE-0260): the generic CLI entrypoints (`run` / `crawl` / `audit` /
    `record`) catch that base, so an Android device failure surfaces the same way — a clean exit-2
    rather than an unhandled traceback — without their handlers importing the iOS `simctl` module.
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


# A device serial / emulator id follows the shared `device_id` policy (never leading with `-`,
# which adb would read as an option). Every command builder validates the serial through `_adb`,
# so an id from `--udid` / config can neither inject an adb option nor reach a subprocess argv
# unchecked. Raises adb's `DeviceError` so a bad serial surfaces as the CLI's clean exit-2.
def _checked_serial(serial: str) -> str:
    if not is_valid_device_id(serial):
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


# The device-side path `screenrecord` writes to before it is pulled to the run dir. One fixed path is
# enough: a device runs one scenario at a time, and parallel lanes are distinct serials. Public so the
# interval starter pulls from and cleans up the same path it records to.
VIDEO_DEVICE_PATH = "/sdcard/bajutsu-scenario.mp4"


def screenrecord_cmd(serial: str, device_path: str = VIDEO_DEVICE_PATH) -> list[str]:
    """Record the screen to `device_path` on the device (h264 mp4); the twin of simctl recordVideo.

    Writes device-side — `screenrecord` cannot stream to a host file — so the recording is pulled off
    after the process stops (the stop/pull lifecycle lives in `intervals.start_screenrecord`).
    """
    return _adb(serial, "shell", "screenrecord", device_path)


def logcat_cmd(serial: str) -> list[str]:
    """Stream the device log to stdout — the twin of simctl `log stream` for `deviceLog`.

    `-T 1` starts the follow from the tail (one recent line) so the capture reflects the scenario
    window rather than dumping the whole ring buffer's pre-run history, mirroring `log stream`'s
    new-events-only semantics. Unfiltered: a logcat tag/priority filterspec is a different syntax
    from the iOS `os_log` predicate, so the predicate is not forwarded here (a tag filter can be a
    later knob).
    """
    return _adb(serial, "logcat", "-T", "1")


def pull_cmd(serial: str, device_path: str, local_path: str) -> list[str]:
    """Copy a device-side file to the host (`adb pull`) — used to collect the recorded video."""
    return _adb(serial, "pull", device_path, local_path)


def rm_cmd(serial: str, device_path: str) -> list[str]:
    """Remove a device-side file (`rm -f`) — cleans up the pulled recording."""
    return _adb(serial, "shell", "rm", "-f", device_path)


def screenrecord_pids_cmd(serial: str) -> list[str]:
    """Device-side `screenrecord` pids on stdout, empty once it has exited.

    Used to tell when the recording is finalized: on the stop signal `screenrecord` keeps writing
    the mp4's `moov` atom device-side after the local `adb shell` client returns, so a pull must
    wait for the process to exit. `|| true` makes a no-match `pgrep` (exit 1) still return 0, so the
    poll reads presence from stdout, not the exit code (the `RunFn` raises on a non-zero exit).
    """
    return _adb(serial, "shell", "pgrep -x screenrecord || true")


KEYCODE_BACK = 4  # `input keyevent` code for the system back button (Android's true system back).
KEYCODE_DEL = 67  # backspace — deletes the character before the cursor (BE-0265 delete / clear).
KEYCODE_CTRL_LEFT = 113  # left Control, the modifier for the select-all / copy key combinations.
KEYCODE_A = 29  # the `A` key — with Ctrl held, "select all" in a focused text field (BE-0265).
KEYCODE_C = 31  # the `C` key — with Ctrl held, "copy" the active selection (BE-0265).


def tap_cmd(serial: str, x: float, y: float) -> list[str]:
    return _adb(serial, "shell", "input", "tap", _num(x), _num(y))


def double_tap_cmd(serial: str, x: float, y: float) -> list[str]:
    """Both taps of a double-tap in a single `adb shell` round-trip (BE-0210) — the non-root fallback.

    Two separate `adb shell input tap` invocations put a whole adb transport round-trip between the
    taps, widening the inter-tap gap past the platform's double-tap window. Chaining both `input tap`
    calls in one round-trip (`input tap x y ; input tap x y`, run by the device shell) removes that
    transport latency; the residual gap is the on-device `input` startup, which stock `input` cannot
    avoid. A rooted device closes that gap instead with `sendevent_double_tap_cmd` (BE-0208); this
    remains the fallback when root or the touchscreen node is unavailable.
    """
    xs, ys = _num(x), _num(y)
    return _adb(serial, "shell", "input", "tap", xs, ys, ";", "input", "tap", xs, ys)


# --- raw touch injection for a reliable double-tap (BE-0208) ---
#
# `input tap x y ; input tap x y` starts a fresh JVM per tap, so the inter-tap gap overruns the
# platform's double-tap window even chained in one round-trip (BE-0210). `sendevent` is a tiny native
# binary, so two contacts fire well inside the window — but it writes `/dev/input` directly, so it
# needs root and the concrete touchscreen node (discovered from `getevent -lp`). The driver gates on
# `id -u` and falls back to `input tap` when either is unavailable, so a non-rooted device is
# unaffected. Linux input protocol B, one finger: type/code constants below name the raw events.
_EV_SYN, _EV_KEY, _EV_ABS = 0, 1, 3
_SYN_REPORT = 0
_BTN_TOUCH = 330
_ABS_MT_SLOT, _ABS_MT_POSITION_X, _ABS_MT_POSITION_Y = 47, 53, 54
_ABS_MT_TRACKING_ID, _ABS_MT_PRESSURE = 57, 58
_TOUCH_PRESSURE = 50  # a nominal non-zero pressure so the contact reads as a real finger
# sendevent parses values as unsigned, so the -1 that lifts a protocol-B contact wraps to 2**32-1.
_MT_TRACKING_ID_LIFT = (1 << 32) - 1
_TAP_TRACKING_IDS = (100, 101)  # a distinct contact id per tap of the double-tap

_ADD_DEVICE = re.compile(r"add device \d+:\s*(\S+)")
_AXIS_MAX = re.compile(r"\bmax (\d+)")
_EVENT_INDEX = re.compile(r"(\d+)$")


@dataclass(frozen=True)
class TouchDevice:
    """A touchscreen `/dev/input` node and its raw coordinate range, from `getevent -lp`."""

    path: str
    max_x: int
    max_y: int


def getevent_probe_cmd(serial: str) -> list[str]:
    """List input devices and their axes (`getevent -lp`) to find the touchscreen; needs no root."""
    return _adb(serial, "shell", "getevent", "-lp")


def id_u_cmd(serial: str) -> list[str]:
    """The shell user id (`id -u`); `"0"` means adbd runs as root, required to write `/dev/input`."""
    return _adb(serial, "shell", "id", "-u")


def parse_touch_device(text: str) -> TouchDevice | None:
    """The touchscreen node from `getevent -lp`: the one exposing both ABS_MT_POSITION axes.

    The Android emulator lists several identical `virtio_input_multi_touch_*` nodes but wires only
    the lowest-numbered `/dev/input/eventN` to the display, so the lowest N among the candidates is
    chosen; a real device has one touchscreen, picked trivially. Returns None when no node carries
    both position axes — there is nothing to drive, and the caller falls back to `input tap`.
    """
    axes: dict[str, dict[str, int]] = {}
    path: str | None = None
    for line in text.splitlines():
        if m := _ADD_DEVICE.search(line):
            path = m.group(1)
        elif path is not None and (mm := _AXIS_MAX.search(line)):
            if "ABS_MT_POSITION_X" in line:
                axes.setdefault(path, {})["x"] = int(mm.group(1))
            elif "ABS_MT_POSITION_Y" in line:
                axes.setdefault(path, {})["y"] = int(mm.group(1))
    candidates = [
        TouchDevice(path=p, max_x=a["x"], max_y=a["y"])
        for p, a in axes.items()
        if "x" in a and "y" in a
    ]
    return min(candidates, key=lambda d: _event_index(d.path), default=None)


def _event_index(path: str) -> int:
    m = _EVENT_INDEX.search(path)
    return int(m.group(1)) if m else 0


def scale_to_touch(
    point: tuple[float, float], screen: tuple[float, float], dev: TouchDevice
) -> tuple[int, int]:
    """Screen-pixel (x, y) into the device's raw coordinate range, proportional on each axis.

    Points on a dense screen and the device's raw range differ per axis, so each is scaled
    independently against its own maximum (a degenerate zero screen extent scales to 0). The result
    is clamped to `[0, max]` so a point resolved just outside the screen extent never sends an
    out-of-range raw coordinate.
    """
    x, y = point
    w, h = screen
    raw_x = round(x / w * dev.max_x) if w else 0
    raw_y = round(y / h * dev.max_y) if h else 0
    return _clamp(raw_x, dev.max_x), _clamp(raw_y, dev.max_y)


def _clamp(value: int, maximum: int) -> int:
    return max(0, min(value, maximum))


def _tap_events(dev: str, x: int, y: int, tracking_id: int) -> list[str]:
    """One protocol-B slot-0 down/up contact at (x, y) as `sendevent` command lines."""
    dev = shlex.quote(dev)
    return [
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} 0",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} {tracking_id}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_X} {x}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_Y} {y}",
        f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 1",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_PRESSURE} {_TOUCH_PRESSURE}",
        f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} {_MT_TRACKING_ID_LIFT}",
        f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 0",
        f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0",
    ]


def sendevent_double_tap_cmd(serial: str, device_path: str, raw_x: int, raw_y: int) -> list[str]:
    """A double-tap as two raw `sendevent` contacts in one `adb shell` round-trip (BE-0208).

    Both contacts fire in a single device shell, so only `sendevent`'s tiny native startup — not a
    per-tap JVM — sits between the taps, keeping the gap inside the platform's double-tap window.
    """
    script = " ; ".join(
        line
        for tracking_id in _TAP_TRACKING_IDS
        for line in _tap_events(device_path, raw_x, raw_y, tracking_id)
    )
    return _adb(serial, "shell", script)


# --- two-contact raw gestures: pinch / rotate (BE-0232) ---
#
# A pinch or a rotate needs two contacts moving at once, which `input` cannot express — so, like the
# double-tap (BE-0210), they go through `sendevent` protocol B, extended from one slot to two. Both
# contacts go down, sweep together across several interleaved SYN_REPORT frames, then lift; a teleport
# reads as a tap, not a gesture, because the platform's GestureDetector needs the motion to classify a
# scale or a rotation. Unlike the double-tap there is no single-touch approximation of two fingers, so
# the driver requires a rooted device and fails loudly otherwise (BE-0232) rather than falling back.
_GESTURE_TRACKING_IDS = (200, 201)  # a distinct contact id per finger (slot 0 / slot 1)
# Interleaved move frames between the down and the up: enough travel for the platform to carry the
# gesture past its touch slop and classify it. A condition wait on the mirrored a11y value — not a
# fixed count — is what proves the gesture landed, so this only shapes the motion, never the verdict.
_GESTURE_STEPS = 8

# A point in tree (pixel) coordinates, and a (slot-0, slot-1) pair of them. Geometry is computed in
# pixel space (below) and scaled per-axis to the device's raw range by the driver, because the raw
# axes are square while the screen is not — rotating in raw space would distort the sweep.
_Point = tuple[float, float]
_Contacts = tuple[_Point, _Point]


def pinch_contacts(center: _Point, half: float, scale: float) -> tuple[_Contacts, _Contacts]:
    """The two contacts' start and end points for a pinch about `center` (BE-0232).

    Both fingers sit level on a line through the centre, `half` out to either side, and move to
    `half * scale`: `scale > 1` spreads them (zoom in), `scale < 1` closes them (zoom out).
    """
    cx, cy = center
    start = ((cx - half, cy), (cx + half, cy))
    end = ((cx - half * scale, cy), (cx + half * scale, cy))
    return start, end


def rotate_contacts(center: _Point, half: float, radians: float) -> tuple[_Contacts, _Contacts]:
    """The two contacts' start and end points for a rotation about `center` (BE-0232).

    Both fingers start level on a diameter, `half` out to either side, and sweep through `radians`
    about the centre (positive is clockwise in screen coordinates, where y grows downward). Only the
    endpoints are returned: `sendevent_gesture_cmd` interpolates each contact along the straight chord
    between them, not the arc — as the web backend's rotate does too. That approximates a real
    rotation for the sub-π turns a rotate gesture uses (the showcase drives ~1 rad); a `|radians| ≥ π`
    turn would collapse the chord through the centre, which is out of scope here.
    """
    cx, cy = center
    start = ((cx - half, cy), (cx + half, cy))
    end = (_rotate_point(start[0], center, radians), _rotate_point(start[1], center, radians))
    return start, end


def _rotate_point(point: _Point, origin: _Point, radians: float) -> _Point:
    """Rotate `point` about `origin` by `radians` (positive is clockwise in screen coordinates)."""
    px, py = point
    ox, oy = origin
    dx, dy = px - ox, py - oy
    c, s = math.cos(radians), math.sin(radians)
    return (ox + dx * c - dy * s, oy + dx * s + dy * c)


def sendevent_gesture_cmd(
    serial: str,
    device_path: str,
    start: _Contacts,
    end: _Contacts,
    steps: int = _GESTURE_STEPS,
) -> list[str]:
    """A two-finger gesture as a raw two-slot protocol-B sequence in one `adb shell` round-trip (BE-0232).

    Both contacts go down, sweep from `start` to `end` across `steps` interleaved SYN_REPORT frames —
    so the platform sees motion, not a teleport, and classifies a scale or a rotation — then lift.
    `start` and `end` are each a (slot-0, slot-1) pair of raw device coordinates. Contacts are already
    scaled into the touch device's raw range by the driver; this only formats the `sendevent` lines.
    """
    dev = shlex.quote(device_path)
    lines: list[str] = []
    for slot, ((x, y), tracking_id) in enumerate(zip(start, _GESTURE_TRACKING_IDS, strict=True)):
        lines += _contact_down(dev, slot, tracking_id, round(x), round(y))
    lines.append(f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 1")  # one press for the whole gesture
    lines.append(f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0")
    for k in range(1, steps + 1):
        t = k / steps
        for slot, ((sx, sy), (ex, ey)) in enumerate(zip(start, end, strict=True)):
            lines += _contact_move(dev, slot, round(sx + (ex - sx) * t), round(sy + (ey - sy) * t))
        lines.append(f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0")
    for slot in (0, 1):
        lines.append(f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} {slot}")
        lines.append(f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} {_MT_TRACKING_ID_LIFT}")
    lines.append(f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 0")  # release once both contacts are up
    lines.append(f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0")
    return _adb(serial, "shell", " ; ".join(lines))


def _contact_down(dev: str, slot: int, tracking_id: int, x: int, y: int) -> list[str]:
    """Place one protocol-B contact in `slot` at (x, y). `dev` is already shell-quoted."""
    return [
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} {slot}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} {tracking_id}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_X} {x}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_Y} {y}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_PRESSURE} {_TOUCH_PRESSURE}",
    ]


def _contact_move(dev: str, slot: int, x: int, y: int) -> list[str]:
    """Move the contact already down in `slot` to (x, y). `dev` is already shell-quoted."""
    return [
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} {slot}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_X} {x}",
        f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_Y} {y}",
    ]


def keyevent_cmd(serial: str, keycode: int) -> list[str]:
    """Inject a hardware/system key event (`input keyevent <code>`).

    The system back button (`KEYCODE_BACK`) has no on-screen element to tap — unlike iOS, whose OS
    back button is an on-screen element — so it is actuated as a key event rather than a coordinate.
    """
    return _adb(serial, "shell", "input", "keyevent", str(keycode))


def keyevents_cmd(serial: str, keycodes: list[int]) -> list[str]:
    """Inject a run of key events in one `input keyevent` call (BE-0265).

    `input keyevent` takes several keycodes at once, so a repeated backspace (delete / clear) is a
    single device round-trip rather than one per character.
    """
    return _adb(serial, "shell", "input", "keyevent", *(str(k) for k in keycodes))


def keycombination_cmd(serial: str, keycodes: list[int]) -> list[str]:
    """Inject a chord (modifier + key) via `input keycombination` (BE-0265).

    Backs the select-all (Ctrl+A) and copy (Ctrl+C) actions on a focused text field. `input
    keycombination` presses the keys together, which the text field reads as the editor shortcut —
    a single deterministic key chord rather than the locale-dependent long-press context menu.
    Needs Android 12 (API 31)+; the concrete select/copy mechanism is finalized per backend at build
    time (BE-0265, following BE-0052's per-primitive triage).
    """
    return _adb(serial, "shell", "input", "keycombination", *(str(k) for k in keycodes))


def swipe_cmd(serial: str, x1: float, y1: float, x2: float, y2: float, ms: int = 300) -> list[str]:
    # A finite duration makes it a real drag; a zero-duration swipe is a fling, not a pan.
    return _adb(serial, "shell", "input", "swipe", _num(x1), _num(y1), _num(x2), _num(y2), str(ms))


def shell_cmd(serial: str) -> list[str]:
    """An `adb shell` with no command — the device command is fed on stdin (see `text_script`)."""
    return _adb(serial, "shell")


def text_script(text: str) -> str:
    """The device-side `input text` command line, safe to feed to `adb shell` over stdin.

    Fed on stdin — not as an `adb` argv token — so a secret / OTP typed by a scenario never appears
    in the host process's command line where `ps` could read it (BE-0155). Spaces become `input`'s `%s`
    escape (it splits its argument on spaces), and the result is single-quoted for the device shell.
    """
    return f"input text {shlex.quote(text.replace(' ', '%s'))}"


def pm_clear_cmd(serial: str, package: str) -> list[str]:
    """Reset the app's data/state — the Android `erase` equivalent."""
    return _adb(serial, "shell", "pm", "clear", package)


def force_stop_cmd(serial: str, package: str) -> list[str]:
    return _adb(serial, "shell", "am", "force-stop", package)


def pm_grant_cmd(serial: str, package: str, permission: str) -> list[str]:
    """Grant a runtime permission up front (`pm grant`), so its prompt never blocks the run.

    Granting the permission deterministically before launch — rather than tapping the runtime
    dialog when it appears — keeps timing off the run path (BE-0210).
    """
    return _adb(serial, "shell", "pm", "grant", package, permission)


def pm_revoke_cmd(serial: str, package: str, permission: str) -> list[str]:
    """Revoke a runtime permission (`pm revoke`), the twin of `pm_grant_cmd` (BE-0276)."""
    return _adb(serial, "shell", "pm", "revoke", package, permission)


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


def install_cmd(serial: str, apk_path: str) -> list[str]:
    # -r reinstall keeping data, -t allow test/debug APKs (the showcase builds are debug).
    return _adb(serial, "install", "-r", "-t", apk_path)


# --- resident UI Automator server (BE-0245) ---

# The resident server's fixed loopback port on the device (matches
# BajutsuAndroidUIAutomatorServer's ResidentServerTest); bajutsu reaches it over `adb forward`.
RESIDENT_DEVICE_PORT = 6790

# The androidTest instrumentation that runs the resident server: the `.test` package (androidx adds
# the suffix to the server's applicationId) driven by AndroidJUnitRunner, scoped to the one blocking
# `serve()` method so `am instrument` starts nothing else.
RESIDENT_INSTRUMENTATION = "dev.bajutsu.android.server.test/androidx.test.runner.AndroidJUnitRunner"
RESIDENT_TEST_METHOD = "dev.bajutsu.android.server.ResidentServerTest#serve"
# The server's own package (its applicationId); force-stopped at lease end to kill any device-side
# instrumentation the local adb client's exit did not.
RESIDENT_SERVER_PACKAGE = "dev.bajutsu.android.server"


def forward_cmd(serial: str, device_port: int = RESIDENT_DEVICE_PORT) -> list[str]:
    """Forward a free host port to the device's resident-server port; adb prints the host port on stdout.

    `tcp:0` asks adb to pick an unused host port, so parallel lanes on distinct serials never contend
    for one fixed port; the caller parses the chosen port from stdout.
    """
    return _adb(serial, "forward", "tcp:0", f"tcp:{device_port}")


def forward_remove_cmd(serial: str, host_port: int) -> list[str]:
    """Tear down the `adb forward` for `host_port`, paired with `forward_cmd` at lease end."""
    return _adb(serial, "forward", "--remove", f"tcp:{host_port}")


def reverse_cmd(serial: str, port: int) -> list[str]:
    """Tunnel a device-side port back to the same host port, so the app can reach the host collector.

    `adb reverse` is the opposite direction of `forward_cmd` (host → device, for the resident server):
    here the emulator's `127.0.0.1:<port>` reaches the `NetworkCollector` bajutsu started on the host's
    loopback (BE-0283). Device and host port are the same, so the injected `BAJUTSU_COLLECTOR` URL
    (`http://127.0.0.1:<port>`) resolves on-device unchanged — no URL rewrite.
    """
    return _adb(serial, "reverse", f"tcp:{port}", f"tcp:{port}")


def reverse_remove_cmd(serial: str, port: int) -> list[str]:
    """Tear down the `adb reverse` for `port`, paired with `reverse_cmd` at lease end."""
    return _adb(serial, "reverse", "--remove", f"tcp:{port}")


def instrument_cmd(serial: str) -> list[str]:
    """Start the resident server by running its blocking `serve()` @Test under `am instrument -w`.

    `-w` keeps the instrumentation attached — `serve()` never returns, holding the `UiAutomation`
    session warm — and `-e class …#serve` scopes the run to that one method.
    """
    return _adb(
        serial,
        "shell",
        "am",
        "instrument",
        "-w",
        "-e",
        "class",
        RESIDENT_TEST_METHOD,
        RESIDENT_INSTRUMENTATION,
    )


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


# --- device control (BE-0211): the emulator-backed subset of the DeviceControl family ---


def geo_fix_cmd(serial: str, lat: float, lon: float) -> list[str]:
    """Set the emulated GPS fix via the emulator console (`emu geo fix`).

    `geo fix` takes `<longitude> <latitude>`, the reverse of `set_location(lat, lon)`, so the two
    are swapped here — the one place the order matters.
    """
    return _adb(serial, "emu", "geo", "fix", str(lon), str(lat))


# Clipboard runs through the app's in-app receiver (BajutsuAndroid), not `cmd clipboard`: on a real
# device / the google_apis image `cmd clipboard set/get-primary-clip` answers "No shell command
# implementation" (exit 0, a silent no-op), and since Android 10 only the foreground app / default
# IME may touch the clipboard, so a shell-uid process cannot (`service call clipboard` hits
# ClipboardService.checkAndSetPrimaryClip and is brittle across API levels) — BE-0233. The app under
# test *is* foreground while a scenario drives it, so bajutsu sends an ordered `am broadcast` to a
# receiver inside the app, which reads/writes the clipboard from the app process and returns the
# value in the broadcast result. `am broadcast` acts as the finish-receiver, so the receiver's
# `setResultCode`/`setResultData` come back on stdout.
CLIPBOARD_ACTION = "dev.bajutsu.CLIPBOARD"

# The receiver sets this result code so a run can tell "the app handled it" from "no receiver was
# present" (am leaves the code at 0). Must match BajutsuAndroid's receiver.
CLIPBOARD_RESULT_OK = 1

_RESULT_CODE_RE = re.compile(r"result=(-?\d+)")
_RESULT_DATA_RE = re.compile(r'data="([^"]*)"')


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode("ascii")


def _clipboard_broadcast_cmd(serial: str, package: str, op: str, *extra: str) -> list[str]:
    # `-p <package>` limits delivery to the app under test's receiver. Payloads travel base64-encoded
    # (a shell-safe alphabet), so `adb shell`'s one-string argv join needs no quoting and no scenario
    # text can execute on the device — the same threat `text_script` guards with `shlex.quote`.
    return _adb(
        serial,
        "shell",
        "am",
        "broadcast",
        "-a",
        CLIPBOARD_ACTION,
        "-p",
        package,
        "--es",
        "op",
        op,
        *extra,
    )


def set_primary_clip_cmd(serial: str, package: str, text: str) -> list[str]:
    """Broadcast a `set` of `text` to the app's clipboard receiver (base64-encoded, see above).

    Empty `text` omits the `b64` extra: `base64("")` is `""`, and `adb shell` drops an empty trailing
    argv element when it rejoins the command string, so the extra would reach `am` value-less and
    error. The receiver reads a missing `b64` as empty, seeding an empty clip.
    """
    extra = ("--es", "b64", _b64(text)) if text else ()
    return _clipboard_broadcast_cmd(serial, package, "set", *extra)


def get_primary_clip_cmd(serial: str, package: str) -> list[str]:
    """Broadcast a `get` to the app's clipboard receiver; the value comes back as broadcast result."""
    return _clipboard_broadcast_cmd(serial, package, "get")


def clear_primary_clip_cmd(serial: str, package: str) -> list[str]:
    """Broadcast a `clear` to the app's clipboard receiver."""
    return _clipboard_broadcast_cmd(serial, package, "clear")


def parse_clipboard_result(out: str) -> str:
    """The clipboard text from an `am broadcast` reply, raising loudly if the app had no receiver.

    `am broadcast` prints the ordered broadcast's final `result=<code>, data="<b64>"`. The
    BajutsuAndroid receiver sets `result=CLIPBOARD_RESULT_OK` and base64-encodes the clip into
    `data`; `get` returns the decoded text, `set` / `clear` return `""`.

    Raises:
        DeviceError: the code is not `CLIPBOARD_RESULT_OK` — the app under test embeds no
            BajutsuAndroid clipboard receiver, so nothing was read or written. Surfaced loudly
            (prime directive 2) rather than the silent empty the old `cmd clipboard` path returned.
    """
    m = _RESULT_CODE_RE.search(out)
    if (int(m.group(1)) if m else 0) != CLIPBOARD_RESULT_OK:
        raise DeviceError(
            "clipboard broadcast was not handled: the app under test has no BajutsuAndroid "
            "clipboard receiver. adb clipboard needs the in-app SDK (see BajutsuAndroid/README.md)."
        )
    d = _RESULT_DATA_RE.search(out)
    return base64.b64decode(d.group(1)).decode() if d else ""  # no data (set/clear) → empty read


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
        # Validate at construction (like AdbDriver): Env is what AndroidEnvironment.start drives
        # for the real device-lifecycle path, so a bad serial fails here, not deep in a command.
        self.serial = _checked_serial(serial)
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

    # Device control: the subset the emulator can honor, the Android peer of simctl's setLocation /
    # clipboard. setLocation is a pure emulator-console op (BE-0211); clipboard goes through the app's
    # in-app receiver (BE-0233), so its methods take the target package to address the broadcast. The
    # rest of the DeviceControl family has no faithful emulator equivalent and is not wired (see
    # `platform_lifecycle.device_control.android_device_control`).

    def set_location(self, lat: float, lon: float) -> None:
        self._run(geo_fix_cmd(self.serial, lat, lon))

    def set_clipboard(self, package: str, text: str) -> None:
        parse_clipboard_result(self._run(set_primary_clip_cmd(self.serial, package, text)))

    def clear_clipboard(self, package: str) -> None:
        parse_clipboard_result(self._run(clear_primary_clip_cmd(self.serial, package)))

    def get_clipboard(self, package: str) -> str:
        return parse_clipboard_result(self._run(get_primary_clip_cmd(self.serial, package)))
