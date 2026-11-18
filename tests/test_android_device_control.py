"""Android device control: the emulator-backed subset of the `DeviceControl` family.

`setLocation` (`emu geo fix`, BE-0211) runs over the emulator console. Clipboard (BE-0233) runs
over an ordered `am broadcast` to an in-app receiver (BajutsuAndroid), because since Android 10 only
the foreground app / default IME may touch the clipboard — `cmd clipboard` answers "No shell command
implementation" on-device, a silent no-op. The backend still advertises `clipboard` (it can drive it
given a cooperating app, as the iOS clipboard rides simctl). `push` needs a receiver in the app's
process and Android has no keychain, so both stay unsupported; `background` and the status bar are in
`test_android_device_state.py` (BE-0478). Fast gate over an injected `adb` runner (no device):
command shape, the broadcast result parse (base64 + loud-fail on no receiver), the clipboard
round-trip against a fake receiver, that the supported subset delegates and the rest raise, and that
the adb backend advertises exactly that subset so preflight (BE-0212) admits it and fails the rest
fast.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path

import pytest

from bajutsu.common import platform_lifecycle
from bajutsu.common.backend_cli import adb
from bajutsu.common.capability import capability_preflight
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver
from bajutsu.common.orchestrator.types import DeviceControl
from bajutsu.common.scenario import Scenario, load_scenarios

_PKG = "com.bajutsu.showcase.android.compose"


def _ok_reply(data: str | None = None) -> str:
    """An `am broadcast` reply the BajutsuAndroid receiver would produce (result OK, optional data)."""
    line = f"Broadcast completed: result={adb.CLIPBOARD_RESULT_OK}"
    return line + (f', data="{data}"' if data is not None else "")


# --- pure command builders ---


def _recorder(calls: list[list[str]]) -> adb.RunFn:
    """An `adb` RunFn that records each argv and returns empty output."""

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return ""

    return run


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


def test_clipboard_broadcast_command_builders() -> None:
    assert adb.set_primary_clip_cmd("E", _PKG, "COUPON123") == [
        "adb", "-s", "E", "shell", "am", "broadcast",
        "-a", "dev.bajutsu.CLIPBOARD", "-p", _PKG,
        "--es", "op", "set",
        "--es", "b64", base64.b64encode(b"COUPON123").decode(),
    ]  # fmt: skip
    # Free-form text travels base64-encoded, so shell metacharacters never reach the device shell —
    # nothing to execute there (the reason the argv needs no `shlex.quote`, unlike `cmd clipboard`).
    danger = adb.set_primary_clip_cmd("E", _PKG, "a; rm -rf /sdcard")
    assert danger[-1] == base64.b64encode(b"a; rm -rf /sdcard").decode()
    assert ";" not in " ".join(danger)
    assert adb.get_primary_clip_cmd("E", _PKG) == [
        "adb", "-s", "E", "shell", "am", "broadcast",
        "-a", "dev.bajutsu.CLIPBOARD", "-p", _PKG, "--es", "op", "get",
    ]  # fmt: skip
    assert adb.clear_primary_clip_cmd("E", _PKG)[-3:] == ["--es", "op", "clear"]


def test_set_clipboard_empty_text_omits_the_b64_extra() -> None:
    # base64("") is "", and `adb shell` drops an empty trailing argv element when it rejoins the
    # command, so an empty clip omits the extra rather than sending a value-less `--es b64` that `am`
    # rejects; the receiver reads a missing `b64` as an empty clip.
    cmd = adb.set_primary_clip_cmd("E", _PKG, "")
    assert "b64" not in cmd
    assert cmd[-3:] == ["--es", "op", "set"]


def test_parse_clipboard_result_decodes_and_loud_fails() -> None:
    got = _ok_reply(base64.b64encode("よ COUPON".encode()).decode())
    assert adb.parse_clipboard_result(got) == "よ COUPON"
    # set / clear replies carry no data → an empty read-back, not a failure.
    assert adb.parse_clipboard_result(_ok_reply()) == ""
    # No receiver in the app: `am` leaves the result at 0, so this is a loud failure (prime
    # directive 2), never the silent empty the old `cmd clipboard` no-op returned.
    with pytest.raises(adb.DeviceError):
        adb.parse_clipboard_result("Broadcast completed: result=0")


# --- adb.Env device-control wrappers over an injected runner ---


def test_env_set_location_runs_geo_fix() -> None:
    calls: list[list[str]] = []
    adb.Env("E", run=_recorder(calls)).set_location(1.0, 2.0)
    assert calls == [["adb", "-s", "E", "emu", "geo", "fix", "2.0", "1.0"]]


def test_env_set_and_clear_clipboard_broadcast_to_the_package() -> None:
    calls: list[list[str]] = []

    def run(a: list[str]) -> str:
        calls.append(a)
        return _ok_reply()

    env = adb.Env("E", run=run)
    env.set_clipboard(_PKG, "COUPON123")
    env.clear_clipboard(_PKG)
    assert calls == [
        adb.set_primary_clip_cmd("E", _PKG, "COUPON123"),
        adb.clear_primary_clip_cmd("E", _PKG),
    ]


def test_env_get_clipboard_decodes_the_broadcast_result() -> None:
    reply = _ok_reply(base64.b64encode(b"COUPON123").decode())
    assert adb.Env("E", run=lambda a: reply).get_clipboard(_PKG) == "COUPON123"


def test_env_clipboard_raises_loudly_when_the_app_has_no_receiver() -> None:
    # A run against an app that never called Bajutsu.startClipboard fails, not silently returns "".
    env = adb.Env("E", run=lambda a: "Broadcast completed: result=0")
    with pytest.raises(adb.DeviceError):
        env.get_clipboard(_PKG)


# --- the in-app location route (BE-0481) ---


def _location_reply(code: int) -> str:
    return f"Broadcast completed: result={code}"


def test_location_builders_address_the_package_with_string_coordinates() -> None:
    assert adb.mock_location_appop_cmd("R", _PKG, "allow") == [
        "adb", "-s", "R", "shell", "appops", "set", _PKG, "android:mock_location", "allow",
    ]  # fmt: skip
    assert adb.location_broadcast_cmd("R", _PKG, "set", -33.8688, 151.2093) == [
        "adb", "-s", "R", "shell", "am", "broadcast", "-a", adb.LOCATION_ACTION, "-p", _PKG,
        "--es", "op", "set", "--es", "lat", "-33.8688", "--es", "lon", "151.2093",
    ]  # fmt: skip
    assert adb.location_broadcast_cmd("R", _PKG, "clear") == [
        "adb", "-s", "R", "shell", "am", "broadcast", "-a", adb.LOCATION_ACTION, "-p", _PKG,
        "--es", "op", "clear",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "build",
    [
        lambda: adb.mock_location_appop_cmd("R", _PKG, "deny"),
        lambda: adb.location_broadcast_cmd("R", _PKG, "get"),
        lambda: adb.location_broadcast_cmd("R", _PKG, "set", 1.0, None),
    ],
)
def test_location_builders_reject_what_the_receiver_would_not_handle(
    build: Callable[[], list[str]],
) -> None:
    with pytest.raises(adb.DeviceError):
        build()


def test_parse_location_result_names_each_cause() -> None:
    adb.parse_location_result(_location_reply(adb.LOCATION_RESULT_OK))
    with pytest.raises(adb.DeviceError, match="mock-location app-op"):
        adb.parse_location_result(_location_reply(adb.LOCATION_RESULT_NO_MOCK_PERMISSION))
    with pytest.raises(adb.DeviceError, match=r"Bajutsu\.startLocation"):
        adb.parse_location_result(_location_reply(0))
    with pytest.raises(adb.DeviceError, match="no BajutsuAndroid location receiver"):
        adb.parse_location_result("")
    with pytest.raises(adb.DeviceError, match=r"rejected the mock location: .*bad provider"):
        adb.parse_location_result(
            f'Broadcast completed: result={adb.LOCATION_RESULT_MOCK_FAILED}, data="bad provider"'
        )
    with pytest.raises(adb.DeviceError, match=r"am replied: 'Error: no such package'"):
        adb.parse_location_result("Error: no such package")


@pytest.mark.parametrize(
    ("serial", "props", "expected", "reads"),
    [
        ("emulator-5554", {"ro.boot.qemu": "1"}, True, 1),
        ("emulator-5554", {"ro.kernel.qemu": "1"}, True, 2),  # an older image
        ("emulator-5554", {}, False, 2),  # a device that merely carries the name
        ("127.0.0.1:5555", {"ro.boot.qemu": "1"}, False, 0),  # `adb connect`: no console to reach
        ("R5CT1234ABC", {}, False, 0),
    ],
)
def test_has_emulator_console_needs_both_the_serial_and_the_property(
    serial: str, props: dict[str, str], expected: bool, reads: int
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return props.get(argv[-1], "") + "\n"

    assert adb.Env(serial, run=run).has_emulator_console() is expected
    assert len(calls) == reads


def test_env_set_mock_location_grants_the_app_op_before_broadcasting() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return _location_reply(adb.LOCATION_RESULT_OK) if "broadcast" in argv else ""

    adb.Env("R", run=run).set_mock_location(_PKG, 35.681, 139.767)
    assert calls == [
        adb.mock_location_appop_cmd("R", _PKG, "allow"),
        adb.location_broadcast_cmd("R", _PKG, "set", 35.681, 139.767),
    ]


def test_env_clear_and_revoke_mock_location() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return _location_reply(adb.LOCATION_RESULT_OK) if "broadcast" in argv else ""

    env = adb.Env("R", run=run)
    env.clear_mock_location(_PKG)
    env.revoke_mock_location(_PKG)
    assert calls == [
        adb.location_broadcast_cmd("R", _PKG, "clear"),
        adb.mock_location_appop_cmd("R", _PKG, "default"),
    ]


def test_env_set_mock_location_surfaces_an_appops_complaint() -> None:
    # `appops set` can print its error and still exit 0, so any output fails the call.
    env = adb.Env("R", run=lambda a: "Error: Unknown package: x" if "appops" in a else "")
    with pytest.raises(adb.DeviceError, match="appops set"):
        env.set_mock_location(_PKG, 1.0, 2.0)


# --- android_device_control: supported subset delegates, the rest raise UnsupportedAction ---


def test_android_control_set_location_delegates_with_longitude_first() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        return "1\n" if "getprop" in argv else ""  # a local emulator image (BE-0481)

    ctrl = platform_lifecycle.android_device_control("emulator-5554", _PKG, env_run=run)
    ctrl.set_location(35.6, 139.7)
    assert calls[-1] == ["adb", "-s", "emulator-5554", "emu", "geo", "fix", "139.7", "35.6"]


def test_android_control_clipboard_round_trip() -> None:
    stored = {"clip": ""}

    def fake_receiver(args: list[str]) -> str:
        # Emulate BajutsuAndroid answering the ordered broadcast over the base64 protocol.
        op = args[args.index("op") + 1]
        if op == "set":
            stored["clip"] = base64.b64decode(args[args.index("b64") + 1]).decode()
            return _ok_reply()
        if op == "clear":
            stored["clip"] = ""
            return _ok_reply()
        return _ok_reply(base64.b64encode(stored["clip"].encode()).decode())  # get

    ctrl = platform_lifecycle.android_device_control("E", _PKG, env_run=fake_receiver)
    ctrl.set_clipboard("COUPON123")
    assert ctrl.get_clipboard() == "COUPON123"
    ctrl.clear_clipboard()
    assert ctrl.get_clipboard() == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.push({"aps": {"alert": "hi"}}),
        lambda c: c.clear_keychain(),
        lambda c: c.foreground(),
    ],
)
def test_android_control_unsupported_operations_raise(
    call: Callable[[DeviceControl], None],
) -> None:
    # Operations the emulator can't honor raise UnsupportedAction — the runtime backstop behind the
    # per-operation preflight (BE-0212), never a silent no-op.
    ctrl = platform_lifecycle.android_device_control("E", _PKG, env_run=lambda a: "")
    with pytest.raises(base.UnsupportedAction):
        call(ctrl)


# --- capability declaration + preflight (BE-0212 tokens) ---


def test_adb_advertises_the_device_control_subset_it_can_honor() -> None:
    caps = AdbDriver("E", run=lambda a: "").capabilities()
    assert base.Capability.DC_SET_LOCATION in caps
    assert base.Capability.DC_CLIPBOARD in caps
    assert base.Capability.DC_FOREGROUND in caps
    assert base.Capability.DC_BACKGROUND in caps
    assert base.Capability.DC_STATUS_BAR in caps
    # push needs a receiver in the app's process, and Android has no keychain to clear.
    assert base.Capability.DC_PUSH not in caps
    assert base.Capability.DC_CLEAR_KEYCHAIN not in caps


def test_adb_advertises_the_whole_permission_vocabulary() -> None:
    # `pm grant`/`pm revoke` back every service, including `notifications` (POST_NOTIFICATIONS,
    # API 33+) — unlike iOS, which has no TCC service for it (BE-0276).
    caps = AdbDriver("E", run=lambda a: "").capabilities()
    for service in base.PERMISSION_SERVICES:
        assert base.permission_capability(service) in caps


def _sc(**body: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **body})


@pytest.mark.parametrize(
    "step",
    [
        {"setLocation": {"lat": 1.0, "lon": 2.0}},
        {"setClipboard": {"text": "x"}},
        {"clearClipboard": {}},
        {"background": {}},
        {"overrideStatusBar": {"time": "9:41"}},
        {"clearStatusBar": {}},
    ],
)
def test_preflight_admits_supported_steps_on_adb(step: dict[str, object]) -> None:
    assert capability_preflight.unsupported(_sc(steps=[step]), AdbDriver.CAPABILITIES) == []


@pytest.mark.parametrize(
    "step",
    [
        {"push": {"payload": {"aps": {"alert": "hi"}}}},
        {"clearKeychain": {}},
    ],
)
def test_preflight_rejects_unsupported_steps_on_adb(step: dict[str, object]) -> None:
    reasons = capability_preflight.unsupported(_sc(steps=[step]), AdbDriver.CAPABILITIES)
    assert len(reasons) == 1
    assert reasons[0].startswith("step 1: ")


# --- the CI-lane scenario itself: device.yaml exercises the emulator subset (BE-0208 unit 5) ---
# device.yaml is the single cross-backend device-environment scenario — `setLocation` and the
# clipboard are advertised by every device backend (clipboard via simctl on iOS, the BajutsuAndroid
# in-app receiver on Android, BE-0233), so one file runs on iOS (XCUITest) and Android (adb)
# alike. The iOS-only `push` half lives in push.yaml, kept out of this shared file because adb does not
# advertise `deviceControl.push` (asserted below), so the shared scenario stays preflight-clean on Android.

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "demos" / "showcase" / "scenarios"
_DEVICE = _SCENARIOS_DIR / "device.yaml"
_PUSH = _SCENARIOS_DIR / "push.yaml"


def _single(path: Path) -> Scenario:
    scenarios = load_scenarios(path.read_text(encoding="utf-8"))
    assert len(scenarios) == 1  # a single flow per file
    return scenarios[0]


def test_device_overrides_location_and_round_trips_the_clipboard() -> None:
    # The shared device-control scenario: setLocation + a clipboard seed/read-back on the Stable launch
    # tab, re-asserting the settled screen. The clipboard rejoined the lane once BajutsuAndroid's in-app
    # receiver made it work on-device (BE-0233); simctl backs it on iOS, so the one file runs on both
    # platforms. The read-back is the strong assertion PR #934 wanted.
    scn = _single(_DEVICE)
    assert any(s.set_location is not None for s in scn.steps), "expected a setLocation step"
    assert any(s.set_clipboard is not None for s in scn.steps), "expected a setClipboard step"
    assert not any(s.push is not None for s in scn.steps), "push lives in push.yaml"
    assert any(a.exists is not None for a in scn.expect), "expected a settled-screen re-assert"
    assert any(a.clipboard is not None for a in scn.expect), "expected a clipboard read-back assert"


def test_device_is_preflight_clean_on_adb() -> None:
    # Every step falls inside the adb backend's advertised capabilities (setLocation + clipboard), so
    # the shared scenario runs on Android with no runtime UnsupportedAction (BE-0212 preflight).
    assert capability_preflight.unsupported(_single(_DEVICE), AdbDriver.CAPABILITIES) == []


def test_push_scenario_is_rejected_by_preflight_on_adb() -> None:
    # push.yaml is iOS-only: adb does not advertise `deviceControl.push`, so preflight fails the
    # scenario fast rather than letting the run reach a runtime UnsupportedAction. This is why the
    # push flow is split out of the shared device.yaml (which stays runnable on Android) (BE-0007).
    # push.yaml carries a second scenario since BE-0416 (the notification-banner fixture), so this
    # selects the original by name rather than assuming the file holds a single flow.
    scenarios = load_scenarios(_PUSH.read_text(encoding="utf-8"))
    scenario = next(s for s in scenarios if s.name == "receive a push notification")
    reasons = capability_preflight.unsupported(scenario, AdbDriver.CAPABILITIES)
    assert any("push" in r for r in reasons), reasons
