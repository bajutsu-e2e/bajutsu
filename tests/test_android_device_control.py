"""Android device control: the emulator-backed subset of the `DeviceControl` family.

`setLocation` (`emu geo fix`, BE-0211) runs over the emulator console. Clipboard (BE-0233) runs
over an ordered `am broadcast` to an in-app receiver (BajutsuAndroid), because since Android 10 only
the foreground app / default IME may touch the clipboard — `cmd clipboard` answers "No shell command
implementation" on-device, a silent no-op. The backend still advertises `clipboard` (it can drive it
given a cooperating app, as the iOS clipboard rides simctl). `push` / `clearKeychain` / the status-bar
overrides / app lifecycle have no faithful equivalent and stay unsupported. Fast gate over an
injected `adb` runner (no device): command shape, the broadcast result parse (base64 + loud-fail on
no receiver), the clipboard round-trip against a fake receiver, that the supported subset delegates
and the rest raise, and that the adb backend advertises exactly `setLocation` + `clipboard` so
preflight (BE-0212) admits those and fails the rest fast.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from bajutsu import adb, capability_preflight, platform_lifecycle
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver
from bajutsu.scenario import Scenario, load_scenarios

_PKG = "com.bajutsu.showcase.android.compose"


def _ok_reply(data: str | None = None) -> str:
    """An `am broadcast` reply the BajutsuAndroid receiver would produce (result OK, optional data)."""
    line = f"Broadcast completed: result={adb.CLIPBOARD_RESULT_OK}"
    return line + (f', data="{data}"' if data is not None else "")


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
    adb.Env("E", run=lambda a: calls.append(a) or "").set_location(1.0, 2.0)
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


# --- android_device_control: supported subset delegates, the rest raise UnsupportedAction ---


def test_android_control_set_location_delegates_with_longitude_first() -> None:
    calls: list[list[str]] = []
    ctrl = platform_lifecycle.android_device_control(
        "E", _PKG, env_run=lambda a: calls.append(a) or ""
    )
    ctrl.set_location(35.6, 139.7)
    assert calls == [["adb", "-s", "E", "emu", "geo", "fix", "139.7", "35.6"]]


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
        lambda c: c.home(),  # `background`
        lambda c: c.foreground(),
        lambda c: c.override_status_bar(time="9:41"),
        lambda c: c.clear_status_bar(),
    ],
)
def test_android_control_unsupported_operations_raise(call) -> None:  # type: ignore[no-untyped-def]
    # Operations the emulator can't honor raise UnsupportedAction — the runtime backstop behind the
    # per-operation preflight (BE-0212), never a silent no-op.
    ctrl = platform_lifecycle.android_device_control("E", _PKG, env_run=lambda a: "")
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
    # push flow is split out of the shared device.yaml (which stays runnable on Android).
    reasons = capability_preflight.unsupported(_single(_PUSH), AdbDriver.CAPABILITIES)
    assert any("push" in r for r in reasons), reasons
