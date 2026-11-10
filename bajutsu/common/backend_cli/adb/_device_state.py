"""adb builders for the simctl-parity device state (BE-0478): Home, the status bar, app locales, appTrace.

Each is the adb counterpart of a `simctl` operation the iOS backend already drives, reached with no
cooperating code inside the app: `input keyevent KEYCODE_HOME` for `simctl`'s Home button, the
System UI demo mode for `simctl status_bar`, `cmd locale` for the app's language, and a tag-filtered
`logcat` for the `os_log` subsystem stream. Builders are pure; `Env` runs them.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from datetime import datetime

from ._functions import _adb
from ._shared import KEYCODE_HOME
from .device_error import DeviceError

# --- background: the Home key, and the probe the step waits on ---


def home_cmd(serial: str) -> list[str]:
    """Press Home: the launcher comes to the front and the app keeps its process (`background`)."""
    return _adb(serial, "shell", "input", "keyevent", str(KEYCODE_HOME))


def resumed_activities_cmd(serial: str) -> list[str]:
    """Dump the activity manager's activity state, whose resumed-activity lines `background` polls."""
    return _adb(serial, "shell", "dumpsys", "activity", "activities")


# `topResumedActivity=ActivityRecord{1a2b u0 com.example/.Main t42}` on API 29 and later;
# `mResumedActivity: ActivityRecord{…}` on earlier levels and per display on a multi-display device.
# Android 12 and later also print `ResumedActivity: ActivityRecord{…}` per task display area. Anchored
# at the line start, so a field such as `mLastResumedActivity` never reads as a resumed one.
_RESUMED = re.compile(
    r"^\s*(?:topResumedActivity|mResumedActivity|ResumedActivity)\s*[=:]\s*"
    r"ActivityRecord\{\S+\s+\S+\s+(?P<package>[\w.]+)/",
    re.MULTILINE,
)


# Any resumed-activity field, whatever it holds (`topResumedActivity=null` included).
_RESUMED_FIELD = re.compile(
    r"^\s*(?:topResumedActivity|mResumedActivity|ResumedActivity)\s*[=:]", re.MULTILINE
)


def resumed_packages(text: str) -> set[str]:
    """Every package that holds a resumed activity in a `dumpsys activity activities` dump.

    `background` waits for its package to be absent here, so a dump this cannot read must fail
    rather than parse to an empty set, which would end that wait at once with the app in front.

    Raises:
        DeviceError: The dump names no resumed-activity field at all.
    """
    if not _RESUMED_FIELD.search(text):
        raise DeviceError("dumpsys activity activities: no resumed-activity field to read")
    return {m.group("package") for m in _RESUMED.finditer(text)}


# --- overrideStatusBar / clearStatusBar: the System UI demo mode ---

_DEMO_ACTION = "com.android.systemui.demo"

# `H:MM` or `HH:MM` alone: the demo clock takes four digits and nothing else (`hhmm`).
_STATUS_BAR_TIME = re.compile(r"^(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)$")
_BATTERY_PLUGGED = {"charging": "true", "charged": "true", "discharging": "false"}
# The step's inclusive range per numeric field, as `simctl status_bar` accepts it.
_STATUS_BAR_RANGES = {
    "battery_level": ("batteryLevel", 0, 100),
    "cellular_bars": ("cellularBars", 0, 4),
    "wifi_bars": ("wifiBars", 0, 3),
}


def status_bar_problems(fields: Mapping[str, str | int]) -> list[str]:
    """Why the demo mode cannot pin *fields* faithfully, one message per field; empty when it can.

    `simctl status_bar` rejects an out-of-range value and fails the step, but `am broadcast` reports
    success whatever its extras say and System UI ignores a value it cannot show, so the step would
    pass with the status bar unpinned. Preflight and the control both run this check (BE-0478).

    Args:
        fields: The step's set fields under their Python names (`battery_level`, `wifi_bars`, …).
    """
    problems: list[str] = []
    time = fields.get("time")
    if time is not None and not _STATUS_BAR_TIME.match(str(time)):
        problems.append(f"time {time!r} must be H:MM or HH:MM on Android")
    state = fields.get("battery_state")
    if state is not None and state not in _BATTERY_PLUGGED:
        problems.append(
            f"batteryState {state!r} must be one of {', '.join(sorted(_BATTERY_PLUGGED))}"
        )
    for key, (name, low, high) in _STATUS_BAR_RANGES.items():
        value = fields.get(key)
        if value is not None and not (isinstance(value, int) and low <= value <= high):
            problems.append(f"{name} {value!r} must be within {low}–{high}")
    return problems


def _demo_cmd(serial: str, command: str, *extras: str) -> list[str]:
    return _adb(
        serial, "shell", "am", "broadcast", "-a", _DEMO_ACTION, "-e", "command", command, *extras
    )


def _wifi_level(bars: int) -> int:
    # iOS shows 0 to 3 Wi-Fi bars and Android 0 to 4 levels; scaling keeps a full iOS signal full.
    return round(bars * 4 / 3)


def status_bar_demo_cmds(serial: str, fields: Mapping[str, str | int]) -> list[list[str]]:
    """The commands that freeze the status bar at *fields*, in the order they must run.

    Allow the demo mode, enter it, hide the notification icons (the iOS status bar shows none), then
    one command per field group the step sets. Entering freezes every icon, so a field left unset
    takes the demo mode's own fixed value rather than the live one.

    Raises:
        DeviceError: A field is outside what the demo mode shows (`status_bar_problems`).
    """
    if problems := status_bar_problems(fields):
        raise DeviceError(f"overrideStatusBar: {'; '.join(problems)}")
    cmds = [
        _adb(serial, "shell", "settings", "put", "global", "sysui_demo_allowed", "1"),
        _demo_cmd(serial, "enter"),
        _demo_cmd(serial, "notifications", "-e", "visible", "false"),
    ]
    time = fields.get("time")
    if time is not None:
        m = _STATUS_BAR_TIME.match(str(time))
        assert m is not None  # status_bar_problems admitted it
        cmds.append(_demo_cmd(serial, "clock", "-e", "hhmm", f"{int(m['h']):02d}{m['m']}"))
    battery: list[str] = []
    if (level := fields.get("battery_level")) is not None:
        battery += ["-e", "level", str(level)]
    if (state := fields.get("battery_state")) is not None:
        battery += ["-e", "plugged", _BATTERY_PLUGGED[str(state)]]
    if battery:
        cmds.append(_demo_cmd(serial, "battery", *battery))
    if (bars := fields.get("cellular_bars")) is not None:
        cmds.append(_demo_cmd(serial, "network", "-e", "mobile", "show", "-e", "level", str(bars)))
    if (bars := fields.get("wifi_bars")) is not None:
        level = _wifi_level(int(bars))
        cmds.append(_demo_cmd(serial, "network", "-e", "wifi", "show", "-e", "level", str(level)))
    return cmds


def status_bar_exit_cmd(serial: str) -> list[str]:
    """Leave the demo mode, restoring the live status bar (`clearStatusBar`)."""
    return _demo_cmd(serial, "exit")


# --- runsOn.android.locale: per-app language preferences (API level 33 and later) ---

# The first API level whose `cmd locale` sets a per-app language.
APP_LOCALES_MIN_API = 33

# The `runsOn.ios.locale` spelling (`ja_JP`, `en-US`), with an optional ICU keyword suffix the
# language tag drops. Never leads with `-`, so the value cannot read as an option.
_LOCALE = re.compile(r"^(?P<tag>[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{1,8})*)(?:@\S*)?$")


def language_tag(locale: str) -> str:
    """The Best Current Practice 47 (BCP 47) language tag for a config locale (`ja_JP` -> `ja-JP`).

    Raises:
        DeviceError: The value is not a locale.
    """
    m = _LOCALE.match(locale)
    if m is None:
        raise DeviceError(f"invalid locale: {locale!r}")
    parts = re.split(r"[_-]", m["tag"])
    # Everything from a singleton (`u`, `x`) on is an extension, which BCP 47 keeps lower case.
    singleton = next((i for i, part in enumerate(parts) if len(part) == 1), len(parts))
    return "-".join(
        _canonical_subtag(i, part) if i < singleton else part.lower()
        for i, part in enumerate(parts)
    )


def _canonical_subtag(index: int, part: str) -> str:
    # BCP 47's canonical case, which `get-app-locales` reports back: `ja`, `Hant`, `JP`, `419`.
    if index == 0:
        return part.lower()
    if len(part) == 4 and part.isalpha():  # a script subtag is four letters
        return part.title()
    if len(part) == 2 and part.isalpha():  # a region subtag is two letters
        return part.upper()
    return part.lower()


def set_app_locales_cmd(serial: str, package: str, tag: str) -> list[str]:
    """Set *package*'s per-app language to *tag*; an empty tag clears it back to the system's.

    One device-shell string with each value quoted: an empty `--locales` would otherwise vanish when
    adb rejoins its argv, and the package arrives from config.
    """
    shell = (
        f"cmd locale set-app-locales {shlex.quote(package)} --user current "
        f"--locales {shlex.quote(tag)}"
    )
    return _adb(serial, "shell", shell)


def get_app_locales_cmd(serial: str, package: str) -> list[str]:
    """Read *package*'s per-app language back (`Locales for <package> for user 0 are [ja-JP]`)."""
    shell = f"cmd locale get-app-locales {shlex.quote(package)} --user current"
    return _adb(serial, "shell", shell)


_APP_LOCALES = re.compile(r"\[(?P<tags>[^\]]*)\]")


def parse_app_locales(text: str) -> str | None:
    """The tag list a `get-app-locales` reply names, comma-joined; None when it names none."""
    m = _APP_LOCALES.search(text)
    return m["tags"].strip() if m is not None else None


# --- appTrace: the app's own log tag, streamed from logcat ---


def logcat_app_trace_cmd(serial: str, tag: str, since: str | None) -> list[str]:
    """Stream the lines logged under *tag* from the device-clock instant *since* onward.

    `-T "<MM-DD hh:mm:ss.mmm>"` lets the device decide which lines precede the stream, so host clock
    skew neither drops a real line nor keeps a stale `started`; with no instant it follows from the
    tail, as `deviceLog` does. Keyed on the tag, not a process id, so a `relaunch` keeps the stream.
    """
    tail = since if since is not None else "1"
    return _adb(serial, "logcat", "-v", "year,usec,zone", "-T", tail, "-s", f"{tag}:V")


# `2026-11-10 12:34:56.123456 +0900  1234  1250 I checkout: checkout started`
_LOGCAT_LINE = re.compile(
    r"^(?P<stamp>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)\s+(?P<zone>[+-]\d{4})\s+"
    r"\d+\s+\d+\s+[VDIWEFA]\s+.*?:\s(?P<message>.*)$"
)


def parse_logcat_line(line: str) -> tuple[datetime, str] | None:
    """The zoned timestamp and message of one `-v year,usec,zone` logcat line, or None."""
    m = _LOGCAT_LINE.match(line.rstrip())
    if m is None:
        return None
    try:
        stamp = datetime.strptime(f"{m['stamp']} {m['zone']}", "%Y-%m-%d %H:%M:%S.%f %z")
    except ValueError:
        return None
    return stamp, m["message"].strip()
