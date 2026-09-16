"""Drive one device through the adb command line, from shell calls up to synthesized touches."""

from __future__ import annotations

import base64
import math
import re
import shlex
import subprocess
from collections.abc import Mapping

from bajutsu.common.devices.id import is_valid_device_id

from ._shared import RunFn
from .device_error import DeviceError
from .touch_device import TouchDevice

# The device-side path `screenrecord` writes to before it is pulled to the run dir. One fixed path is
# enough: a device runs one scenario at a time, and parallel lanes are distinct serials. Public so the
# interval starter pulls from and cleans up the same path it records to.
VIDEO_DEVICE_PATH = "/sdcard/bajutsu-scenario.mp4"


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


# --- resident UI Automator server (BE-0245) ---

# The resident server's fixed loopback port on the device (matches
# BajutsuAndroidUIAutomatorServer's ResidentServerTest); bajutsu reaches it over `adb forward`.
RESIDENT_DEVICE_PORT = 6790

# The androidTest instrumentation that runs the resident server: the `.test` package (androidx adds
# the suffix to the server's applicationId) driven by AndroidJUnitRunner, scoped to the one blocking
# `serve()` method so `am instrument` starts nothing else.
RESIDENT_INSTRUMENTATION = "dev.bajutsu.android.server.test/androidx.test.runner.AndroidJUnitRunner"
RESIDENT_TEST_METHOD = "dev.bajutsu.android.server.ResidentServerTest#serve"


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


def device_error(exc: subprocess.CalledProcessError) -> DeviceError:
    """Turn a raw adb failure into a clean DeviceError, keeping the command, exit code, and stderr."""
    cmd = exc.cmd if isinstance(exc.cmd, str) else " ".join(map(str, exc.cmd or []))
    # stderr is str under our text=True runs, but decode bytes too (a text=False caller) so the most
    # actionable part of the failure is never silently dropped.
    stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
    detail = ((stderr if isinstance(stderr, str) else "") or "").strip()
    msg = f"device operation failed (exit {exc.returncode}): {cmd}"
    return DeviceError(f"{msg}\n{detail}" if detail else msg)


def real_run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def _num(v: float) -> str:
    return str(round(v))  # `input tap`/`swipe` take integer coordinates


# A device serial / emulator id follows the shared `device_id` policy (never leading with `-`,
# which adb would read as an option). Every command builder validates the serial through `_adb`,
# so an id from `--udid` / config can neither inject an adb option nor reach a subprocess argv
# unchecked. Raises adb's `DeviceError` so a bad serial surfaces as the CLI's clean exit-2.
def checked_serial(serial: str) -> str:
    if not is_valid_device_id(serial):
        raise DeviceError(f"invalid device serial: {serial!r}")
    return serial


def _adb(serial: str, *rest: str) -> list[str]:
    return ["adb", "-s", checked_serial(serial), *rest]


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


def screenrecord_cmd(
    serial: str,
    device_path: str = VIDEO_DEVICE_PATH,
    *,
    time_limit: int | None = None,
    size: str | None = None,
    bit_rate: int | None = None,
) -> list[str]:
    """Record the screen to `device_path` on the device (h264 mp4); the twin of simctl recordVideo.

    Writes device-side — `screenrecord` cannot stream to a host file — so the recording is pulled off
    after the process stops (the stop/pull lifecycle lives in `intervals.start_screenrecord`). The
    keyword-only options forward to `screenrecord`'s own flags and are omitted (device defaults) when
    left `None`: `time_limit` bounds a recording that would otherwise stop at the 180s default,
    `size`/`bit_rate` shrink the mp4 below the 20 Mbps full-resolution default — a caller with a
    multi-minute window and a size-conscious artifact upload (the Android CI codegen lane) sets all
    three; a scenario-length `bajutsu run` capture does not need them (BE-0350).
    """
    cmd = _adb(serial, "shell", "screenrecord")
    if time_limit is not None:
        cmd += ["--time-limit", str(time_limit)]
    if size is not None:
        cmd += ["--size", size]
    if bit_rate is not None:
        cmd += ["--bit-rate", str(bit_rate)]
    cmd.append(device_path)
    return cmd


def logcat_cmd(serial: str) -> list[str]:
    """Stream the device log to stdout — the twin of simctl `log stream` for `deviceLog`.

    `-T 1` follows from the tail (one recent line), mirroring `log stream`'s new-events-only
    semantics. Unfiltered: a logcat tag/priority filterspec is a different syntax from the iOS
    `os_log` predicate, so it is not forwarded here.

    `-b main,system,crash,events` widens past bare `logcat`'s default by adding `events`: an
    `ActivityManager` kill for memory pressure logs only there (`am_kill`/`am_low_memory`), never
    through `crash`, so omitting it would make that cause indistinguishable from a silent failure.
    The kernel's own OOM/LMK path (`/proc/kmsg`) is left out here, not unreachable (docs/evidence.md).
    """
    return _adb(serial, "logcat", "-b", "main,system,crash,events", "-T", "1")


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


def file_size_cmd(serial: str, device_path: str) -> list[str]:
    """The byte size of a device-side file on stdout — `stat -c %s`, falling back to `ls -l`.

    Used to tell whether a recording is actually producing bytes, not merely running (BE-0367).
    `stat` is toybox-provided on modern Android but not guaranteed on every image, so `ls -l`
    stands behind it and `parse_file_size` reads either shape. The trailing `|| true` keeps the
    exit code 0 when the file does not exist yet — the ordinary case before the first byte lands —
    so the poll reads the size from stdout rather than classifying an exception (the `RunFn` raises
    on a non-zero exit).
    """
    quoted = shlex.quote(device_path)
    return _adb(
        serial, "shell", f"stat -c %s {quoted} 2>/dev/null || ls -l {quoted} 2>/dev/null || true"
    )


def parse_file_size(text: str) -> int | None:
    """The byte size in `file_size_cmd`'s output, or None when neither form answered.

    `stat -c %s` prints the size alone; toybox `ls -l` prints it as the fifth field (mode, links,
    owner, group, size). None means "no size to report" — an absent file, or an image where both
    probes failed — which a caller must not read as zero bytes.
    """
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        candidate = fields[0] if len(fields) == 1 else fields[4] if len(fields) >= 5 else None
        # `isascii()` guards `isdigit()`, which is True for fullwidth and superscript digits that
        # `int()` then rejects or misreads.
        if candidate is not None and candidate.isascii() and candidate.isdigit():
            return int(candidate)
    return None


def dumpsys_surfaceflinger_latency_cmd(serial: str) -> list[str]:
    """SurfaceFlinger's per-frame latency table — the compositor's own view of recent frames.

    A stall-time probe (BE-0367): a wedged renderer shows no recent frames here, which separates it
    from a host that is merely starved.
    """
    return _adb(serial, "shell", "dumpsys", "SurfaceFlinger", "--latency")


def logcat_tail_cmd(serial: str, lines: int = 200) -> list[str]:
    """The most recent `lines` of the device's retained log — a dump (`-d`), not a follow.

    The stall-probe counterpart of `logcat_cmd`'s stream: it reads the ring buffer the device
    already holds, so it still has something to show at a moment no scenario-scoped stream covers.
    """
    return _adb(serial, "logcat", "-d", "-t", str(lines))


def tap_cmd(serial: str, x: float, y: float) -> list[str]:
    return _adb(serial, "shell", "input", "tap", _num(x), _num(y))


def wm_size_cmd(serial: str) -> list[str]:
    """`adb shell wm size` — the display resolution in raw pixels, the true viewport (BE-0326)."""
    return _adb(serial, "shell", "wm", "size")


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


def getevent_probe_cmd(serial: str) -> list[str]:
    """List input devices and their axes (`getevent -lp`) to find the touchscreen; needs no root."""
    return _adb(serial, "shell", "getevent", "-lp")


def id_u_cmd(serial: str) -> list[str]:
    """The shell user id (`id -u`); `"0"` means adbd runs as root, required to write `/dev/input`."""
    return _adb(serial, "shell", "id", "-u")


def pidof_cmd(serial: str, package: str) -> list[str]:
    """The package's live process ids, empty when it holds none (BE-0424).

    Note for the caller: toybox `pidof` exits 1 on no match rather than returning empty stdout, and
    the default `RunFn` is `check=True` — so the routine, expected outcome this probe exists to
    observe arrives as a `CalledProcessError`, not as an empty string.
    """
    return _adb(serial, "shell", "pidof", package)


def exit_info_cmd(serial: str, package: str) -> list[str]:
    """The platform's own `ApplicationExitInfo` history for one package (BE-0424).

    Scoped to `package` deliberately: with no package argument `dumpsys activity exit-info` reports
    every package on the device, so the newest entry would be some other process's. API 30+ only —
    the caller checks that before reaching here rather than polling a signal that cannot exist.
    """
    return _adb(serial, "shell", "dumpsys", "activity", "exit-info", package)


def launch_marker_cmd(serial: str) -> list[str]:
    """One device-clock read yielding the three renderings a launch marker needs (BE-0424).

    Epoch, `dumpsys activity exit-info`'s `timestamp=` rendering, and `logcat -t`'s — taken together
    in one call rather than as three `date` invocations, which would stamp three different instants
    and let the `logcat` one filter out a crash that landed in the gap between reads. The format
    string is quoted for the *device* shell, which would otherwise read the `|` as a pipe and
    word-split the rest.

    The three cannot be derived from one another on the host: resolving a device-side rendering into
    an epoch would run it through the *host's* timezone, so a UTC emulator driven from any other zone
    would place every fresh timestamp hours from the marker. Splitting these three fields is a plain
    string split, never a timezone resolution, which is what keeps that rule intact.
    """
    return _adb(serial, "shell", "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'")


def logcat_crash_dump_cmd(serial: str, since: str) -> list[str]:
    """A one-shot dump of the crash buffer since *since* (BE-0424).

    `-d` dumps and exits rather than following. `-t "<MM-DD hh:mm:ss.mmm>"` is a *time* bound only in
    its quoted-string form — `adb logcat -t` reads a bare integer as a line count instead, which is
    why the launch marker's epoch cannot be threaded through here. Nothing is cleared: the buffer is
    device-global and `scripts/collect_android_diagnostics.sh` still sweeps it whole at end of job.
    """
    return _adb(serial, "logcat", "-b", "crash", "-d", "-t", since)


def root_cmd(serial: str) -> list[str]:
    """Restart `adbd` as root (`adb root`); device-wide until `adb unroot` or a reboot."""
    return _adb(serial, "root")


def unroot_cmd(serial: str) -> list[str]:
    """Restart `adbd` unprivileged again (`adb unroot`), handing the device back as it was found."""
    return _adb(serial, "unroot")


def wait_for_device_cmd(serial: str) -> list[str]:
    """Block until `adbd` is answering again — the gate every `adb root`/`unroot` needs after it."""
    return _adb(serial, "wait-for-device")


def tombstones_cmd(serial: str) -> list[str]:
    """Device tombstones as `<device-epoch> <path>` lines, root-gated (BE-0424).

    `stat -c "%Y %n"` rather than `ls -lt`: the caller compares these against the epoch half of its
    launch marker, and both are then the device's own seconds-since-epoch — a plain number
    comparison, never a rendering the host would have to resolve through *its* timezone. An empty
    directory makes the glob fail, so the `|| true` keeps that routine case off the error path.
    """
    return _adb(serial, "shell", 'stat -c "%Y %n" /data/tombstones/tombstone_* 2>/dev/null || true')


def cat_cmd(serial: str, device_path: str) -> list[str]:
    """One device-side file's contents on stdout — the tombstone read, which needs root."""
    return _adb(serial, "shell", "cat", device_path)


# `AppExitInfoTracker.dumpLocked` (frameworks/base) prints entries newest first, each framed as its
# own `ApplicationExitInfo` block, one field per line — `timestamp=` and `reason=` among them, in an
# order and an exact punctuation around `reason=`'s value (`reasonCodeToString(mReason)` alone, or
# with the numeric `Reason` constant alongside it) that varies across AOSP revisions and cannot be
# pinned to one literal shape without a live capture off a real device. Splitting on this marker is
# what lets `timestamp=` and `reason=` be read in either order within the entry that actually carries
# them, rather than pairing one entry's reason with another's timestamp; matching `reason=`'s value by
# known token rather than by a fixed separator is what keeps the read from depending on a punctuation
# guess.
_EXIT_INFO_ENTRY = re.compile(r"ApplicationExitInfo")

# The token `reasonCodeToString` renders for each `ApplicationExitInfo.REASON_*` constant this item
# cares about. Matched as a substring of whatever follows `reason=` on its own line, not as the whole
# remainder, so a leading numeric code and/or surrounding punctuation (`4 CRASH`, `CRASH (4)`,
# `CRASH(4)`) all match the same way without needing to know which shape the device renders.
# `CRASH_NATIVE` is checked before `CRASH`, since it names a superset of the same characters.
_EXIT_INFO_REASON_TOKENS = ("CRASH_NATIVE", "CRASH", "ANR", "LOW_MEMORY", "USER_REQUESTED")


def _exit_info_reason(text: str) -> str | None:
    """The known reason token `text` (the remainder of a `reason=` line) names, or None."""
    upper = text.upper()
    return next((token for token in _EXIT_INFO_REASON_TOKENS if token in upper), None)


def newest_exit_info(text: str) -> tuple[str, str] | None:
    """The `(reason, timestamp)` of the newest `ApplicationExitInfo` entry, or None (BE-0424).

    `dumpsys activity exit-info` prints the history newest first, each entry carrying a `reason=` and
    a `timestamp=`. Only the newest is read: an older entry is an earlier lifetime's, which is what
    the caller's time bound exists to rule out. `timestamp=` is a wall-clock rendering in the
    *device's* own timezone carrying no offset, so it is returned as the string it is — resolving it
    into an epoch on the host would run it through the host's timezone instead.
    """
    starts = [m.start() for m in _EXIT_INFO_ENTRY.finditer(text)]
    ends = [*starts[1:], len(text)]
    blocks = [text] if not starts else [text[a:b] for a, b in zip(starts, ends, strict=True)]
    for block in blocks:  # newest first, per dumpsys's own ordering
        reason_line = re.search(r"\breason=([^\r\n]*)", block)
        stamp = re.search(r"\btimestamp=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", block)
        if reason_line is None or stamp is None:
            continue
        reason = _exit_info_reason(reason_line.group(1))
        if reason is not None:
            return reason, stamp.group(1)
    return None


# What `ApplicationExitInfo` calls a crash, managed and native. `ANR`, `LOW_MEMORY` and
# `USER_REQUESTED` are deliberately not here: each is a real termination the app did not fault on.
EXIT_INFO_CRASH_REASONS = frozenset({"CRASH", "CRASH_NATIVE"})


def extract_crash_block(text: str, package: str) -> str | None:
    """The `logcat` crash block belonging to *package*, managed or native, or None (BE-0424).

    Two formats, because the crash buffer carries both: a managed (Java/Kotlin) `FATAL EXCEPTION`
    block, and an NDK crash's `Fatal signal` block under its own `>>> <process> <<<` header. Each is
    bounded to the app under test's own process, not to the caller's time window alone — the buffer
    is device-global across processes as well as across launches, so a system service faulting in the
    same window must never be written as this scenario's evidence.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if "FATAL EXCEPTION" in line or ">>> " in line]
    for i, start in enumerate(starts):
        # Bounded by whichever comes first: the next block's own header, or the line cap — never
        # past the next header, which is what keeps a block from absorbing a second crash that
        # follows it closely in the device-global buffer.
        end = min(start + _CRASH_BLOCK_LINES, starts[i + 1] if i + 1 < len(starts) else len(lines))
        body = "\n".join(lines[start:end])
        if f"Process: {package}" in body or f">>> {package} <<<" in body:
            return body
    return None


# How many lines of a `logcat` crash block are kept. A managed stack trace with its `Caused by`
# chain, or a native block through its backtrace, both fit comfortably; the cap is what keeps an
# unrelated flood after the block from being copied in as though it were part of it.
_CRASH_BLOCK_LINES = 200


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


def install_cmd(serial: str, apk_path: str) -> list[str]:
    # -r reinstall keeping data, -t allow test/debug APKs (the showcase builds are debug).
    return _adb(serial, "install", "-r", "-t", apk_path)


def uninstall_cmd(serial: str, package: str) -> list[str]:
    return _adb(serial, "uninstall", package)


def package_path_cmd(serial: str, package: str) -> list[str]:
    """Ask where a package's APK sits on the device; empty output means it is not installed.

    `pm path` rather than `pm list packages`, because it names one package directly instead of
    filtering a list the device has to build.
    """
    return _adb(serial, "shell", "pm", "path", package)


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

    That sameness is what constrains the caller: adbd binds `port` inside the guest, so a port free on
    the host is not enough — `NetworkCollector.start_bridgeable` picks one outside both ephemeral
    ranges so the guest cannot already be holding it.
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
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def booted_serials(run: RunFn = real_run) -> list[str]:
    """Serials of the currently-attached, ready devices/emulators (empty on any failure)."""
    try:
        return _parse_devices(run(devices_cmd()))
    except (subprocess.CalledProcessError, OSError):
        return []


def resolve_serial(serial: str, run: RunFn = real_run) -> str:
    """Resolve the alias "booted" to a concrete serial (the first ready device).

    A concrete serial passes through unchanged; "booted" picks the single ready device (the first
    if several). Falls back to "booted" if resolution fails, so the caller fails loudly downstream
    rather than silently targeting the wrong device.
    """
    if serial != "booted":
        return serial
    found = booted_serials(run)
    return found[0] if found else serial


def device_catalog(run: RunFn = real_run) -> dict[str, dict[str, str]]:
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
