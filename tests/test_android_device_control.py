"""Android device control (BE-0211): the emulator-backed subset of the `DeviceControl` family.

The emulator honors `setLocation` (`emu geo fix`) and the clipboard operations (`cmd clipboard`);
`push` / `clearKeychain` / the status-bar overrides / app lifecycle have no faithful equivalent and
stay unsupported. Fast gate over an injected `adb` runner (no device): command shape, the clipboard
read-back round-trip, that the supported subset delegates and the rest raise, and that the adb
backend advertises exactly `setLocation` + `clipboard` so preflight (BE-0212) admits those and fails
the rest fast.
"""

from __future__ import annotations

import pytest

from bajutsu import adb, capability_preflight, platform_lifecycle
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver
from bajutsu.scenario import Scenario

# --- pure command builders ---


def test_geo_fix_command_builder_puts_longitude_before_latitude() -> None:
    # `emu geo fix` takes <longitude> <latitude>, the reverse of DeviceControl.set_location(lat, lon).
    assert adb.geo_fix_cmd("emulator-5554", 35.6, 139.7) == [
        "adb",
        "-s",
        "emulator-5554",
        "emu",
        "geo",
        "fix",
        "139.7",
        "35.6",
    ]


def test_clipboard_command_builders() -> None:
    assert adb.set_primary_clip_cmd("E", "COUPON123") == [
        "adb",
        "-s",
        "E",
        "shell",
        "cmd",
        "clipboard",
        "set-primary-clip",
        "COUPON123",
    ]
    assert adb.get_primary_clip_cmd("E") == [
        "adb",
        "-s",
        "E",
        "shell",
        "cmd",
        "clipboard",
        "get-primary-clip",
    ]
    assert adb.clear_primary_clip_cmd("E") == [
        "adb",
        "-s",
        "E",
        "shell",
        "cmd",
        "clipboard",
        "clear-primary-clip",
    ]


# --- adb.Env device-control wrappers over an injected runner ---


def test_env_set_location_runs_geo_fix() -> None:
    calls: list[list[str]] = []
    adb.Env("E", run=lambda a: calls.append(a) or "").set_location(1.0, 2.0)
    assert calls == [["adb", "-s", "E", "emu", "geo", "fix", "2.0", "1.0"]]


def test_env_set_and_clear_clipboard_run_commands() -> None:
    calls: list[list[str]] = []
    env = adb.Env("E", run=lambda a: calls.append(a) or "")
    env.set_clipboard("COUPON123")
    env.clear_clipboard()
    assert calls == [
        ["adb", "-s", "E", "shell", "cmd", "clipboard", "set-primary-clip", "COUPON123"],
        ["adb", "-s", "E", "shell", "cmd", "clipboard", "clear-primary-clip"],
    ]


def test_env_get_clipboard_returns_stripped_stdout() -> None:
    # get-primary-clip returns the content on stdout; the device shell appends a trailing newline,
    # so it is stripped to match the seeded text on the read-back path.
    assert adb.Env("E", run=lambda a: "COUPON123\n").get_clipboard() == "COUPON123"


# --- android_device_control: supported subset delegates, the rest raise UnsupportedAction ---


def test_android_control_set_location_delegates_with_longitude_first() -> None:
    calls: list[list[str]] = []
    ctrl = platform_lifecycle.android_device_control("E", env_run=lambda a: calls.append(a) or "")
    ctrl.set_location(35.6, 139.7)
    assert calls == [["adb", "-s", "E", "emu", "geo", "fix", "139.7", "35.6"]]


def test_android_control_clipboard_round_trip() -> None:
    stored = {"clip": ""}

    def fake_run(args: list[str]) -> str:
        if "set-primary-clip" in args:
            stored["clip"] = args[-1]
        elif "clear-primary-clip" in args:
            stored["clip"] = ""
        return stored["clip"] + "\n"  # get-primary-clip returns the content with a trailing newline

    ctrl = platform_lifecycle.android_device_control("E", env_run=fake_run)
    ctrl.set_clipboard("COUPON123")
    assert ctrl.get_clipboard() == "COUPON123"
    ctrl.clear_clipboard()
    assert ctrl.get_clipboard() == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.push({"aps": {"alert": "hi"}}),
        lambda c: c.clear_keychain(),
        lambda c: c.home(),  # `background`
        lambda c: c.foreground(),
        lambda c: c.override_status_bar(time="9:41"),
        lambda c: c.clear_status_bar(),
    ],
)
def test_android_control_unsupported_operations_raise(call) -> None:  # type: ignore[no-untyped-def]
    # Operations the emulator can't honor raise UnsupportedAction — the runtime backstop behind the
    # per-operation preflight (BE-0212), never a silent no-op.
    ctrl = platform_lifecycle.android_device_control("E", env_run=lambda a: "")
    with pytest.raises(base.UnsupportedAction):
        call(ctrl)


# --- capability declaration + preflight (BE-0212 tokens) ---


def test_adb_advertises_setlocation_and_clipboard_only() -> None:
    caps = AdbDriver("E", run=lambda a: "").capabilities()
    assert base.Capability.DC_SET_LOCATION in caps
    assert base.Capability.DC_CLIPBOARD in caps
    # The rest of the family stays unadvertised — the emulator has no faithful equivalent.
    assert base.Capability.DC_PUSH not in caps
    assert base.Capability.DC_CLEAR_KEYCHAIN not in caps
    assert base.Capability.DC_APP_LIFECYCLE not in caps
    assert base.Capability.DC_STATUS_BAR not in caps


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **body})


@pytest.mark.parametrize(
    "step",
    [
        {"setLocation": {"lat": 1.0, "lon": 2.0}},
        {"setClipboard": {"text": "x"}},
        {"clearClipboard": {}},
    ],
)
def test_preflight_admits_supported_steps_on_adb(step: dict[str, object]) -> None:
    assert capability_preflight.unsupported(_sc(steps=[step]), AdbDriver.CAPABILITIES) == []


@pytest.mark.parametrize(
    "step",
    [
        {"push": {"payload": {"aps": {"alert": "hi"}}}},
        {"clearKeychain": {}},
        {"background": {}},
        {"overrideStatusBar": {"time": "9:41"}},
    ],
)
def test_preflight_rejects_unsupported_steps_on_adb(step: dict[str, object]) -> None:
    reasons = capability_preflight.unsupported(_sc(steps=[step]), AdbDriver.CAPABILITIES)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 1: ")
