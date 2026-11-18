"""The simctl-parity device state on Android (BE-0478): `background` and the status bar.

`background` presses Home and waits, on a condition, until the app no longer holds the resumed
activity; the status-bar pair drives the System UI demo mode, whose `am broadcast` succeeds whatever
its extras say, so every value it cannot show fails before any device work and again at the control.
Fast gate over an injected `adb` runner (no device).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.common import platform_lifecycle
from bajutsu.common.backend_cli import adb
from bajutsu.common.capability import capability_preflight
from bajutsu.common.config import AndroidConfig, Effective
from bajutsu.common.drivers import base
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.platform_lifecycle.environments.android import android_environment
from bajutsu.common.scenario import Redact, Scenario

_PKG = "com.bajutsu.showcase.android.compose"
_RESUMED_APP = f"  topResumedActivity=ActivityRecord{{4f1c u0 {_PKG}/.MainActivity t12}}\n"
_RESUMED_LAUNCHER = "  topResumedActivity=ActivityRecord{9e2 u0 com.google.android.apps.nexuslauncher/.Launcher t1}\n"


def _recorder(
    calls: list[list[str]], reply: Callable[[list[str]], str] = lambda a: ""
) -> adb.RunFn:
    def run(argv: list[str]) -> str:
        calls.append(argv)
        return reply(argv)

    return run


def _eff() -> Effective:
    return Effective(
        target="showcase-compose",
        platform_config=AndroidConfig(package=_PKG),
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


# --- background ---


def test_home_presses_keycode_home() -> None:
    assert adb.home_cmd("E") == ["adb", "-s", "E", "shell", "input", "keyevent", "3"]


@pytest.mark.parametrize(
    ("dump", "packages"),
    [
        (_RESUMED_APP, {_PKG}),
        (_RESUMED_LAUNCHER, {"com.google.android.apps.nexuslauncher"}),
        # API 28 and earlier name it `mResumedActivity:`.
        (f"    mResumedActivity: ActivityRecord{{1 u0 {_PKG}/.Main t3}}\n", {_PKG}),
        ("  topResumedActivity=null\n", set()),
    ],
)
def test_resumed_packages_reads_the_activity_dump(dump: str, packages: set[str]) -> None:
    assert adb.resumed_packages(dump) == packages


def test_resumed_packages_refuses_a_dump_with_no_resumed_field() -> None:
    with pytest.raises(adb.DeviceError, match="no resumed-activity field"):
        adb.resumed_packages("ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)\n")


def test_background_fails_the_step_on_an_unreadable_dump() -> None:
    env = _env_with_dumps(["garbled\n"], [])
    control = env.controller(_eff())
    assert control is not None
    with pytest.raises(base.UnsupportedAction, match="no resumed-activity field"):
        control.home()


def test_control_without_an_environment_presses_home_alone() -> None:
    calls: list[list[str]] = []
    platform_lifecycle.android_device_control("E", _PKG, _recorder(calls)).home()
    assert calls == [adb.home_cmd("E")]


def _env_with_dumps(dumps: list[str], calls: list[list[str]]) -> AndroidEnvironment:
    replies = iter(dumps)

    def reply(argv: list[str]) -> str:
        return next(replies, dumps[-1]) if "dumpsys" in argv else ""

    return AndroidEnvironment("adb", "E", adb_run=_recorder(calls, reply))


def test_background_waits_until_the_app_leaves_the_front() -> None:
    calls: list[list[str]] = []
    env = _env_with_dumps([_RESUMED_APP, _RESUMED_LAUNCHER], calls)
    control = env.controller(_eff())
    assert control is not None
    control.home()
    assert calls[0] == adb.home_cmd("E")
    # Polled until the dump no longer names the app: once while it is still resumed, once after.
    assert calls[1:] == [adb.resumed_activities_cmd("E")] * 2


def test_background_fails_the_step_when_the_app_never_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(android_environment, "_BACKGROUND_TIMEOUT", 0.05)
    monkeypatch.setattr(android_environment, "_BACKGROUND_POLL", 0.01)
    monkeypatch.setattr(android_environment, "_BACKGROUND_POLL_MAX", 0.01)
    env = _env_with_dumps([_RESUMED_APP], [])
    control = env.controller(_eff())
    assert control is not None
    with pytest.raises(base.UnsupportedAction, match="still in front"):
        control.home()


def test_background_keeps_the_process_so_the_exit_poll_stays_quiet() -> None:
    # Home leaves the process alive: the probe the driver's exit poll reads (`pidof`) still answers,
    # so a backgrounded app is never reported as exited.
    calls: list[list[str]] = []
    env = _env_with_dumps([_RESUMED_LAUNCHER], calls)
    control = env.controller(_eff())
    assert control is not None
    control.home()
    joined = [" ".join(c) for c in calls]
    assert not any("force-stop" in j or "am kill" in j for j in joined)


# --- overrideStatusBar / clearStatusBar ---


def _demo(command: str, *extras: str) -> list[str]:
    return [
        "adb", "-s", "E", "shell", "am", "broadcast", "-a", "com.android.systemui.demo",
        "-e", "command", command, *extras,
    ]  # fmt: skip


_PREAMBLE = [
    ["adb", "-s", "E", "shell", "settings", "put", "global", "sysui_demo_allowed", "1"],
    _demo("enter"),
    _demo("notifications", "-e", "visible", "false"),
]


@pytest.mark.parametrize(
    ("fields", "commands"),
    [
        ({"time": "9:41"}, [_demo("clock", "-e", "hhmm", "0941")]),
        ({"time": "21:05"}, [_demo("clock", "-e", "hhmm", "2105")]),
        ({"battery_level": 80}, [_demo("battery", "-e", "level", "80")]),
        ({"battery_state": "charging"}, [_demo("battery", "-e", "plugged", "true")]),
        ({"battery_state": "charged"}, [_demo("battery", "-e", "plugged", "true")]),
        ({"battery_state": "discharging"}, [_demo("battery", "-e", "plugged", "false")]),
        (
            {"battery_level": 100, "battery_state": "charged"},
            [_demo("battery", "-e", "level", "100", "-e", "plugged", "true")],
        ),
        ({"cellular_bars": 3}, [_demo("network", "-e", "mobile", "show", "-e", "level", "3")]),
        ({"wifi_bars": 0}, [_demo("network", "-e", "wifi", "show", "-e", "level", "0")]),
        ({"wifi_bars": 1}, [_demo("network", "-e", "wifi", "show", "-e", "level", "1")]),
        ({"wifi_bars": 2}, [_demo("network", "-e", "wifi", "show", "-e", "level", "3")]),
        ({"wifi_bars": 3}, [_demo("network", "-e", "wifi", "show", "-e", "level", "4")]),
        ({}, []),
    ],
)
def test_status_bar_maps_each_field_to_a_demo_command(
    fields: dict[str, str | int], commands: list[list[str]]
) -> None:
    assert adb.status_bar_demo_cmds("E", fields) == [*_PREAMBLE, *commands]


@pytest.mark.parametrize(
    "fields",
    [
        {"time": "9:41 AM"},
        {"time": "941"},
        {"time": "24:00"},
        {"time": "Tuesday"},
        {"battery_level": 101},
        {"battery_level": -1},
        {"cellular_bars": 5},
        {"wifi_bars": 4},
        {"battery_state": "unknown"},
    ],
)
def test_status_bar_values_the_demo_mode_cannot_show_are_refused(
    fields: dict[str, str | int],
) -> None:
    assert len(adb.status_bar_problems(fields)) == 1
    with pytest.raises(adb.DeviceError):
        adb.status_bar_demo_cmds("E", fields)
    # The control's backstop fails the step before any broadcast.
    calls: list[list[str]] = []
    control = platform_lifecycle.android_device_control("E", _PKG, _recorder(calls))
    with pytest.raises(base.UnsupportedAction, match="overrideStatusBar"):
        control.override_status_bar(**fields)
    assert calls == []


def test_control_overrides_and_clears_the_status_bar() -> None:
    calls: list[list[str]] = []
    control = platform_lifecycle.android_device_control("E", _PKG, _recorder(calls))
    control.override_status_bar(time="9:41")
    control.clear_status_bar()
    assert calls == [*_PREAMBLE, _demo("clock", "-e", "hhmm", "0941"), _demo("exit")]


def _sc(*steps: dict[str, object]) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": list(steps)})


def test_preflight_names_each_status_bar_value_android_cannot_show() -> None:
    sc = _sc(
        {"overrideStatusBar": {"time": "9:41", "wifiBars": 3}},
        {"overrideStatusBar": {"time": "9:41 AM", "cellularBars": 7}},
    )
    problems = capability_preflight.android_status_bar_problems(sc)
    assert len(problems) == 2
    assert all(p.startswith("step 2: overrideStatusBar ") for p in problems)


def test_preflight_walks_into_nested_and_lifecycle_steps() -> None:
    sc = Scenario.model_validate(
        {
            "name": "s",
            "steps": [{"tap": {"id": "a"}}],
            "cleanup": [{"on": "always", "steps": [{"overrideStatusBar": {"batteryLevel": 120}}]}],
        }
    )
    assert capability_preflight.android_status_bar_problems(sc) == [
        "cleanup[0] [0]: overrideStatusBar batteryLevel 120 must be within 0–100"
    ]


def test_run_preflight_applies_the_value_check_to_adb_alone() -> None:
    from bajutsu.common.runner.pipeline import _step_value_problems

    sc = _sc({"overrideStatusBar": {"time": "9:41 AM"}})
    assert "time '9:41 AM' must be H:MM or HH:MM" in _step_value_problems("adb", sc)
    # iOS keeps `simctl status_bar`'s own wider `time`.
    assert _step_value_problems("xcuitest", sc) == ""


# --- runsOn.android.locale ---


@pytest.mark.parametrize(
    ("locale", "tag"),
    [("ja_JP", "ja-JP"), ("en-US", "en-US"), ("fr", "fr"), ("en_US@calendar=japanese", "en-US")],
)
def test_language_tag_converts_to_bcp47(locale: str, tag: str) -> None:
    assert adb.language_tag(locale) == tag


@pytest.mark.parametrize("locale", ["-x", "ja JP", "", "ja_JP;rm"])
def test_language_tag_refuses_a_non_locale(locale: str) -> None:
    with pytest.raises(adb.DeviceError, match="invalid locale"):
        adb.language_tag(locale)


def test_set_app_locales_quotes_an_empty_tag_for_the_device_shell() -> None:
    assert adb.set_app_locales_cmd("E", _PKG, "")[-1] == (
        f"cmd locale set-app-locales {_PKG} --user current --locales ''"
    )


def _locale_env(reply_locales: str, calls: list[list[str]]) -> adb.Env:
    def reply(argv: list[str]) -> str:
        if "get-app-locales" in argv[-1]:
            return f"Locales for {_PKG} for user 0 are [{reply_locales}]\n"
        return ""

    return adb.Env("E", run=_recorder(calls, reply))


def test_apply_app_locale_sets_then_reads_back() -> None:
    calls: list[list[str]] = []
    _locale_env("ja-JP", calls).apply_app_locale(_PKG, "ja_JP", 34)
    assert calls == [
        adb.set_app_locales_cmd("E", _PKG, "ja-JP"),
        adb.get_app_locales_cmd("E", _PKG),
    ]


def test_apply_app_locale_fails_on_a_read_back_mismatch() -> None:
    with pytest.raises(adb.DeviceError, match=r"reports locale 'en-US' after setting 'ja-JP'"):
        _locale_env("en-US", []).apply_app_locale(_PKG, "ja_JP", 34)


@pytest.mark.parametrize(("level", "words"), [(32, "API level 32"), (None, "unreadable")])
def test_apply_app_locale_fails_below_api_33_or_on_an_unreadable_level(
    level: int | None, words: str
) -> None:
    calls: list[list[str]] = []
    with pytest.raises(adb.DeviceError, match=words):
        _locale_env("", calls).apply_app_locale(_PKG, "ja_JP", level)
    assert calls == []


@pytest.mark.parametrize(("level", "issued"), [(33, True), (35, True), (32, False), (None, False)])
def test_unset_locale_resets_on_api_33_and_later_alone(level: int | None, issued: bool) -> None:
    calls: list[list[str]] = []
    _locale_env("", calls).apply_app_locale(_PKG, None, level)
    cleared = [adb.set_app_locales_cmd("E", _PKG, ""), adb.get_app_locales_cmd("E", _PKG)]
    assert calls == (cleared if issued else [])


def test_a_clear_the_device_ignores_fails_the_start() -> None:
    with pytest.raises(adb.DeviceError, match="reports locale 'ja-JP' after clearing"):
        _locale_env("ja-JP", []).apply_app_locale(_PKG, None, 34)


def _android_eff(locale: str | None, api_level: str | None) -> Effective:
    from dataclasses import replace

    conditions: dict[str, str | list[str]] = {"apiLevel": api_level} if api_level else {}
    return replace(
        _eff(), platform_config=AndroidConfig(package=_PKG, locale=locale), conditions=conditions
    )


@pytest.mark.parametrize(
    ("locale", "api_level", "fails"),
    [
        ("ja_JP", ">=30", True),
        ("ja_JP", "32", True),
        ("ja_JP", "30 - 34", True),
        ("ja_JP", ">=33", False),
        ("ja_JP", "34", False),
        ("ja_JP", None, False),  # checked on the device at start instead
        (None, ">=30", False),
    ],
)
def test_config_stage_fails_a_locale_whose_api_range_admits_an_earlier_level(
    locale: str | None, api_level: str | None, fails: bool
) -> None:
    from bajutsu.common.config.preconditions import check_preconditions
    from bajutsu.common.scenario import Preconditions

    problems = check_preconditions(Preconditions(), [_android_eff(locale, api_level)])
    assert bool(problems) is fails
    if fails:
        assert "runsOn.android.locale" in problems[0]
        assert "runsOn.android.apiLevel" in problems[0]


def test_config_stage_reads_the_scenario_locale_too() -> None:
    from bajutsu.common.config.preconditions import check_preconditions
    from bajutsu.common.scenario import Preconditions

    pre = Preconditions.model_validate({"device": {"android": {"locale": "ja_JP"}}})
    assert check_preconditions(pre, [_android_eff(None, ">=30")])


def _start_run(api: str | None, calls: list[list[str]]) -> adb.RunFn:
    # A device that holds whatever per-app locale was set last, as `cmd locale` does.
    held = [""]

    def reply(argv: list[str]) -> str:
        if "set-app-locales" in argv[-1]:
            held[0] = argv[-1].rsplit("--locales ", 1)[1].strip("'")
        if "sys.boot_completed" in argv:
            return "1\n"
        if "ro.build.version.sdk" in argv:
            if api is None:
                raise OSError("no device")
            return f"{api}\n"
        if "resolve-activity" in argv:
            return f"{_PKG}/.MainActivity\n"
        if "get-app-locales" in argv[-1]:
            return f"Locales for {_PKG} for user 0 are [{held[0]}]\n"
        return ""

    return _recorder(calls, reply)


def _locale_calls(calls: list[list[str]]) -> list[str]:
    return [c[-1] for c in calls if c[-1].startswith("cmd locale")]


def test_start_sets_the_scenario_locale_over_the_target_before_launch() -> None:
    from bajutsu.common.config import ResolvedPreconditions

    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "E", adb_run=_start_run("34", calls))
    env.start(_android_eff("fr_FR", None), ResolvedPreconditions(locale="ja_JP"))
    assert _locale_calls(calls)[0].endswith("--locales ja-JP")
    launch = next(i for i, c in enumerate(calls) if "am" in c and "start" in c)
    assert calls.index(adb.set_app_locales_cmd("E", _PKG, "ja-JP")) < launch


def test_start_resets_an_unset_locale_and_warns_on_an_unreadable_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from bajutsu.common.config import ResolvedPreconditions

    calls: list[list[str]] = []
    AndroidEnvironment("adb", "E", adb_run=_start_run("34", calls)).start(
        _android_eff(None, None), ResolvedPreconditions()
    )
    assert _locale_calls(calls) == [
        adb.set_app_locales_cmd("E", _PKG, "")[-1],
        adb.get_app_locales_cmd("E", _PKG)[-1],
    ]

    calls = []
    with caplog.at_level("WARNING"):
        AndroidEnvironment("adb", "E", adb_run=_start_run(None, calls)).start(
            _android_eff(None, None), ResolvedPreconditions()
        )
    assert _locale_calls(calls) == []
    assert "skipping the per-app locale reset" in caplog.text


def test_start_fails_a_locale_on_an_old_device() -> None:
    from bajutsu.common.config import ResolvedPreconditions

    env = AndroidEnvironment("adb", "E", adb_run=_start_run("31", []))
    with pytest.raises(adb.DeviceError, match="API level 31"):
        env.start(_android_eff("ja_JP", None), ResolvedPreconditions())


def test_scenario_android_locale_reaches_the_app_but_not_the_alert_locale() -> None:
    from bajutsu.common.config import resolve_preconditions
    from bajutsu.common.scenario import Preconditions

    pre = Preconditions.model_validate({"device": {"android": {"locale": "ja_JP"}}})
    assert resolve_preconditions(_android_eff(None, None), pre).locale == "ja_JP"
    # System dialogs keep the emulator's language, so the alert-label locale stays the target's.
    assert pre.resolved_locale("en_US", "android") == "en_US"


# --- appTrace over tag-filtered logcat ---

_LOGCAT_SAMPLE = """\
--------- beginning of main
2026-11-10 12:34:56.100000 +0900  4321  4321 I com.bajutsu.showcase.android.compose: checkout started
2026-11-10 12:34:56.200000 +0900  4321  4330 D com.bajutsu.showcase.android.compose: unrelated line
2026-11-10 12:34:57.350000 +0900  4321  4330 I com.bajutsu.showcase.android.compose: checkout finished
2026-11-10 12:34:58.000000 +0900  4321  4321 I com.bajutsu.showcase.android.compose: sync started
"""


def test_logcat_app_trace_pairs_markers_into_intervals() -> None:
    from bajutsu.common.evidence import intervals

    assert intervals.parse_logcat_app_trace(_LOGCAT_SAMPLE) == [
        {
            "name": "checkout",
            "begin": "2026-11-10T12:34:56.100000+09:00",
            "end": "2026-11-10T12:34:57.350000+09:00",
            "durationMs": 1250.0,
        }
    ]


def test_logcat_line_parser_ignores_noise() -> None:
    assert adb.parse_logcat_line("--------- beginning of main") is None
    assert adb.parse_logcat_line("garbage") is None


def test_logcat_app_trace_cmd_filters_to_the_tag_from_a_device_instant() -> None:
    assert adb.logcat_app_trace_cmd("E", "com.x", "11-10 12:34:56.000") == [
        "adb", "-s", "E", "logcat", "-v", "year,usec,zone",
        "-T", "11-10 12:34:56.000", "-s", "com.x:V",
    ]  # fmt: skip
    # No device instant: follow from the tail, as `deviceLog` does.
    assert adb.logcat_app_trace_cmd("E", "com.x", None)[6:8] == ["-T", "1"]


def test_adb_driver_streams_app_trace_from_the_device_clock(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from bajutsu.common.drivers.adb import AdbDriver
    from bajutsu.common.evidence import intervals

    spawned: list[list[str]] = []

    class _Proc:
        def poll(self) -> int | None:
            return None

    def fake_spawn(argv: list[str], _path: Path | None) -> _Proc:
        spawned.append(argv)
        return _Proc()

    def run(argv: list[str]) -> str:
        return "1762745696|2026-11-10 12:34:56|11-10 12:34:56.000\n" if "date" in argv[-1] else ""

    driver = AdbDriver("E", run=run)
    original = intervals.start_logcat_app_trace

    def start(*args: object, **kwargs: object) -> object:
        return original(*args, spawn=fake_spawn, **kwargs)  # type: ignore[arg-type, misc]

    root = Path(str(tmp_path))
    monkeypatch.setattr(intervals, "start_logcat_app_trace", start)
    interval = driver.driver_interval(
        "appTrace", root / "raw.log", subsystem=_PKG, json_path=root / "appTrace.json"
    )
    assert interval is not None
    assert interval.kind == "appTrace"
    assert spawned == [adb.logcat_app_trace_cmd("E", _PKG, "11-10 12:34:56.000")]
    # Without a subsystem or a JSON path there is nothing to filter or parse into.
    assert driver.driver_interval("appTrace", root / "raw.log") is None


def test_web_and_live_drivers_ignore_the_app_trace_arguments(tmp_path: object) -> None:
    from pathlib import Path

    from bajutsu.common.drivers.playwright.playwright_driver import PlaywrightDriver
    from bajutsu.common.drivers.xcuitest_live.xcuitest_live_driver import XcuitestLiveDriver

    root = Path(str(tmp_path))
    live = XcuitestLiveDriver.__new__(XcuitestLiveDriver)
    assert live.driver_interval("appTrace", root / "a", subsystem="x", json_path=root / "b") is None
    web = PlaywrightDriver.__new__(PlaywrightDriver)
    assert web.driver_interval("appTrace", root / "a", subsystem="x", json_path=root / "b") is None


@pytest.mark.parametrize("driver", ["AdbDriver", "PlaywrightDriver", "XcuitestLiveDriver"])
def test_every_driver_interval_lane_accepts_the_app_trace_arguments(driver: str) -> None:
    # The pool reaches the lane through `getattr`, so no type check holds a driver to the sink's
    # `DriverInterval` shape; this does, for every driver that supplies a lane (BE-0478).
    import inspect

    from bajutsu.common.drivers.adb import AdbDriver
    from bajutsu.common.drivers.playwright.playwright_driver import PlaywrightDriver
    from bajutsu.common.drivers.xcuitest_live.xcuitest_live_driver import XcuitestLiveDriver

    lanes: dict[str, Callable[..., object]] = {
        "AdbDriver": AdbDriver.driver_interval,
        "PlaywrightDriver": PlaywrightDriver.driver_interval,
        "XcuitestLiveDriver": XcuitestLiveDriver.driver_interval,
    }
    params = inspect.signature(lanes[driver]).parameters
    for name in ("subsystem", "json_path"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_sink_hands_app_trace_its_subsystem_and_json_path(tmp_path: object) -> None:
    from pathlib import Path

    from bajutsu.common.evidence.core.file_sink import FileSink

    seen: list[tuple[str, dict[str, object]]] = []

    def lane(kind: str, path: Path, **kwargs: object) -> None:
        seen.append((kind, kwargs))

    root = Path(str(tmp_path))
    FileSink(root, log_subsystem=_PKG, driver_interval=lane).start_scenario_intervals(
        "s1", ["appTrace", "deviceLog"]
    )
    (kind, kwargs), (other, plain) = seen
    assert (kind, kwargs["subsystem"]) == ("appTrace", _PKG)
    assert str(kwargs["json_path"]).endswith("s1/appTrace.json")
    assert (other, plain) == ("deviceLog", {})
    # No subsystem: appTrace has nothing to stream, so the lane is never asked.
    seen.clear()
    FileSink(root, driver_interval=lane).start_scenario_intervals("s2", ["appTrace"])
    assert seen == []


def test_android_target_defaults_the_app_trace_tag_to_its_package() -> None:
    from bajutsu.cli._shared import _log_subsystem_default

    assert _log_subsystem_default(_eff()) == _PKG


@pytest.mark.parametrize(
    ("locale", "tag"),
    [
        ("ja_jp", "ja-JP"),
        ("ZH_hant_TW", "zh-Hant-TW"),
        ("es_419", "es-419"),
        ("en_US_u_ca_gregory", "en-US-u-ca-gregory"),
    ],
)
def test_language_tag_takes_the_canonical_case_the_device_reports(locale: str, tag: str) -> None:
    assert adb.language_tag(locale) == tag


def test_resumed_packages_ignores_a_last_resumed_field() -> None:
    # Android 12 and later print a per-display `ResumedActivity:` line; a `mLastResumedActivity`
    # field names the app that was resumed before Home and must not keep it "in front".
    dump = (
        "  Display #0 (activities from top to bottom):\n"
        "    ResumedActivity: ActivityRecord{9e2 u0 com.google.android.apps.nexuslauncher/.Launcher t1}\n"
        f"    mLastResumedActivity: ActivityRecord{{4f1c u0 {_PKG}/.MainActivity t12}}\n"
        "  topResumedActivity=ActivityRecord{9e2 u0 com.google.android.apps.nexuslauncher/.Launcher t1}\n"
    )
    assert adb.resumed_packages(dump) == {"com.google.android.apps.nexuslauncher"}


def test_install_without_data_pins_the_start_locale_again(tmp_path: object) -> None:
    from pathlib import Path

    from bajutsu.common.config import ResolvedPreconditions

    apk = Path(str(tmp_path)) / "app.apk"
    apk.write_bytes(b"")
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "E", adb_run=_start_run("34", calls))
    eff = _android_eff("fr_FR", None)
    from dataclasses import replace

    eff = replace(
        eff, platform_config=AndroidConfig(package=_PKG, locale="fr_FR", app_path=str(apk))
    )
    env.start(eff, ResolvedPreconditions(locale="ja_JP", reinstall="overwrite"))
    calls.clear()
    env.install_member(eff, keep_data=False)
    # The scenario's locale, which the start applied, not the target's.
    assert _locale_calls(calls)[0].endswith("--locales ja-JP")
    calls.clear()
    env.install_member(eff, keep_data=True)
    assert _locale_calls(calls) == []


def test_member_start_applies_the_member_locale() -> None:
    from bajutsu.common.config import ResolvedPreconditions

    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "E", adb_run=_start_run("34", calls))
    env.start(_android_eff(None, None), ResolvedPreconditions())
    calls.clear()
    from dataclasses import replace

    member = replace(_android_eff("ja_JP", None), target="member")
    env.start_member(member, ResolvedPreconditions(), install=False)
    assert _locale_calls(calls)[0].endswith("--locales ja-JP")


@pytest.mark.parametrize("reply", [OSError("adb gone"), "garbled\n"])
def test_app_trace_falls_back_to_the_tail_and_warns_without_a_device_clock(
    reply: object, tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:

    from bajutsu.common.drivers.adb import AdbDriver

    def run(argv: list[str]) -> str:
        if "date" in argv[-1]:
            if isinstance(reply, Exception):
                raise reply
            return str(reply)
        return ""

    with caplog.at_level("WARNING"):
        assert AdbDriver("E", run=run)._device_now() is None
    assert "appTrace" in caplog.text


def test_logcat_app_trace_interval_parses_the_stream_on_stop(tmp_path: object) -> None:
    from pathlib import Path

    from bajutsu.common.evidence import intervals

    class _Proc:
        def stop(self, sig: int, timeout: float) -> None:
            return None

        def await_stderr(self, needle: str, timeout: float) -> float | None:
            return None

    root = Path(str(tmp_path))
    raw, out = root / "appTrace.log", root / "appTrace.json"
    raw.write_text(_LOGCAT_SAMPLE, encoding="utf-8")
    interval = intervals.start_logcat_app_trace(
        "E", raw, out, _PKG, None, spawn=lambda argv, path: _Proc()
    )
    assert interval.stop() == out
    assert '"name": "checkout"' in out.read_text(encoding="utf-8")
