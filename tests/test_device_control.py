"""Tests for device control primitives (env simctl wrappers + setLocation/push steps)."""

from __future__ import annotations

import json

import pytest

from bajutsu import simctl
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import AlertGuardConfig, run_scenario
from bajutsu.scenario import Foreground, Push, Scenario, SetClipboard, SetLocation, Step

# --- pure command builders ---


def test_location_and_push_command_builders() -> None:
    assert simctl.set_location_cmd("U", 35.6, 139.7) == [
        "xcrun",
        "simctl",
        "location",
        "U",
        "set",
        "35.6,139.7",
    ]
    assert simctl.clear_location_cmd("U") == ["xcrun", "simctl", "location", "U", "clear"]
    assert simctl.push_cmd("U", "com.demo", "/tmp/p.apns") == [
        "xcrun",
        "simctl",
        "push",
        "U",
        "com.demo",
        "/tmp/p.apns",
    ]


def test_env_push_writes_payload_and_runs() -> None:
    calls: list[list[str]] = []
    written: dict[str, object] = {}

    def fake_run(args: list[str], _extra: object = None) -> str:
        calls.append(args)
        # The payload file is the last arg; capture its contents before cleanup.
        with open(args[-1], encoding="utf-8") as f:
            written.update(json.load(f))
        return ""

    simctl.Env("U", run=fake_run).push("com.demo", {"aps": {"alert": "hi"}})
    assert calls and calls[0][:5] == ["xcrun", "simctl", "push", "U", "com.demo"]
    assert written == {"aps": {"alert": "hi"}}


def test_env_set_location_runs_command() -> None:
    calls: list[list[str]] = []
    simctl.Env("U", run=lambda a, _e=None: calls.append(a) or "").set_location(1.0, 2.0)
    assert calls == [["xcrun", "simctl", "location", "U", "set", "1.0,2.0"]]


def test_foreground_command_builder() -> None:
    # resume a backgrounded app: launch WITHOUT --terminate (not a relaunch)
    assert simctl.foreground_cmd("U", "com.demo") == ["xcrun", "simctl", "launch", "U", "com.demo"]


def test_env_foreground_runs_command() -> None:
    calls: list[list[str]] = []
    simctl.Env("U", run=lambda a, _e=None: calls.append(a) or "").foreground("com.demo")
    assert calls == [["xcrun", "simctl", "launch", "U", "com.demo"]]


def test_env_set_clipboard_seeds_pasteboard_with_text(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_pbcopy(cmd: list[str], text: str = "") -> None:
        captured["cmd"], captured["text"] = cmd, text

    monkeypatch.setattr(simctl.Env, "_run_pbcopy", staticmethod(fake_pbcopy))
    simctl.Env("U").set_clipboard("COUPON123")
    assert captured["cmd"] == ["xcrun", "simctl", "pbcopy", "U"]
    assert captured["text"] == "COUPON123"


# --- permissions (simctl privacy, BE-0276) ---


def test_privacy_command_builder() -> None:
    assert simctl.privacy_cmd("U", "grant", "camera", "com.demo") == [
        "xcrun",
        "simctl",
        "privacy",
        "U",
        "grant",
        "camera",
        "com.demo",
    ]


def test_env_apply_permissions_runs_privacy_per_entry() -> None:
    calls: list[list[str]] = []
    simctl.Env("U", run=lambda a, _e=None: calls.append(a) or "").apply_permissions(
        "com.demo", {"camera": "grant", "location": "revoke"}
    )
    assert calls == [
        ["xcrun", "simctl", "privacy", "U", "grant", "camera", "com.demo"],
        ["xcrun", "simctl", "privacy", "U", "revoke", "location", "com.demo"],
    ]


def test_env_apply_permissions_fails_clean_on_notifications() -> None:
    # No TCC service backs iOS notification authorization; preflight rejects this per-service
    # before any device work, but the Env method is the runtime backstop.
    with pytest.raises(simctl.DeviceError, match="notifications"):
        simctl.Env("U", run=lambda a, _e=None: "").apply_permissions(
            "com.demo", {"notifications": "grant"}
        )


def test_env_apply_permissions_validates_before_touching_the_device() -> None:
    # An unsupported service anywhere in the mapping fails before any simctl privacy call runs —
    # never partway through, leaving some services already mutated (BE-0276).
    calls: list[list[str]] = []
    with pytest.raises(simctl.DeviceError, match="notifications"):
        simctl.Env("U", run=lambda a, _e=None: calls.append(a) or "").apply_permissions(
            "com.demo", {"camera": "grant", "notifications": "grant"}
        )
    assert calls == []


def test_env_apply_permissions_validates_action_before_touching_the_device() -> None:
    # An unrecognized action anywhere in the mapping must also fail before any simctl privacy call
    # runs — not just an unsupported service — so an earlier, otherwise-valid entry is never
    # mutated ahead of a later entry's bad action (BE-0276).
    calls: list[list[str]] = []
    with pytest.raises(simctl.DeviceError, match="unknown simctl privacy action"):
        simctl.Env("U", run=lambda a, _e=None: calls.append(a) or "").apply_permissions(
            "com.demo", {"camera": "grant", "microphone": "bogus"}
        )
    assert calls == []


# --- step dispatch through an injected DeviceControl ---


class _RecordingControl:
    def __init__(self) -> None:
        self.locations: list[tuple[float, float]] = []
        self.pushes: list[dict[str, object]] = []
        self.home_calls: int = 0
        self.status_bar_overrides: list[dict[str, str | int]] = []
        self.clear_status_bar_calls: int = 0
        self.clipboards: list[str] = []
        self.clipboard_value: str = ""
        self.foreground_calls: int = 0

    def set_location(self, lat: float, lon: float) -> None:
        self.locations.append((lat, lon))

    def push(self, payload: dict[str, object]) -> None:
        self.pushes.append(payload)

    def clear_keychain(self) -> None:
        pass

    def clear_clipboard(self) -> None:
        pass

    def home(self) -> None:
        self.home_calls += 1

    def override_status_bar(self, **kwargs: str | int) -> None:
        self.status_bar_overrides.append(dict(kwargs))

    def clear_status_bar(self) -> None:
        self.clear_status_bar_calls += 1

    def set_clipboard(self, text: str) -> None:
        self.clipboards.append(text)

    def get_clipboard(self) -> str:
        return self.clipboard_value

    def foreground(self) -> None:
        self.foreground_calls += 1


def test_steps_dispatch_to_control() -> None:
    ctrl = _RecordingControl()
    scn = Scenario(
        name="s",
        steps=[
            Step(set_location=SetLocation(lat=35.6, lon=139.7)),
            Step(push=Push(payload={"aps": {"alert": "ping"}})),
        ],
    )
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.locations == [(35.6, 139.7)]
    assert ctrl.pushes == [{"aps": {"alert": "ping"}}]


def test_background_dispatches_to_control() -> None:
    from bajutsu.scenario import Background

    ctrl = _RecordingControl()
    scn = Scenario(name="s", steps=[Step(background=Background())])
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.home_calls == 1


def test_override_status_bar_dispatches_to_control() -> None:
    from bajutsu.scenario import OverrideStatusBar

    ctrl = _RecordingControl()
    scn = Scenario(
        name="s",
        steps=[Step(override_status_bar=OverrideStatusBar(time="9:41", battery_level=100))],
    )
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.status_bar_overrides == [{"time": "9:41", "battery_level": 100}]


def test_clear_status_bar_dispatches_to_control() -> None:
    from bajutsu.scenario import ClearStatusBar

    ctrl = _RecordingControl()
    scn = Scenario(name="s", steps=[Step(clear_status_bar=ClearStatusBar())])
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.clear_status_bar_calls == 1


def test_set_clipboard_dispatches_to_control() -> None:
    ctrl = _RecordingControl()
    scn = Scenario(name="s", steps=[Step(set_clipboard=SetClipboard(text="COUPON123"))])
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.clipboards == ["COUPON123"]


def test_set_clipboard_parses_camelcase_alias() -> None:
    step = Step.model_validate({"setClipboard": {"text": "hi"}})
    assert step.set_clipboard is not None and step.set_clipboard.text == "hi"


def test_foreground_dispatches_to_control() -> None:
    ctrl = _RecordingControl()
    scn = Scenario(name="s", steps=[Step(foreground=Foreground())])
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok
    assert ctrl.foreground_calls == 1


def test_device_step_without_control_fails_cleanly() -> None:
    scn = Scenario(name="s", steps=[Step(set_location=SetLocation(lat=1.0, lon=2.0))])
    result = run_scenario(FakeDriver(), scn)
    assert not result.ok
    assert "setLocation" in (result.failure or "")


# --- clipboard read-back (BE-0052) ---


def test_pbpaste_command_builder() -> None:
    assert simctl.pbpaste_cmd("U") == ["xcrun", "simctl", "pbpaste", "U"]


def test_env_get_clipboard_returns_stdout() -> None:
    # Env.get_clipboard returns pbpaste's stdout via the injected RunFn (no real simctl).
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: object = None) -> str:
        calls.append(args)
        return "COUPON123"

    assert simctl.Env("U", run=fake_run).get_clipboard() == "COUPON123"
    assert calls == [["xcrun", "simctl", "pbpaste", "U"]]


def test_clipboard_assertion_reads_through_control() -> None:
    ctrl = _RecordingControl()
    ctrl.clipboard_value = "COUPON123"
    scn = Scenario.model_validate(
        {"name": "s", "steps": [{"assert": [{"clipboard": {"equals": "COUPON123"}}]}]}
    )
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert result.ok


def test_clipboard_assertion_mismatch_fails() -> None:
    ctrl = _RecordingControl()
    ctrl.clipboard_value = "WRONG"
    scn = Scenario.model_validate(
        {"name": "s", "steps": [{"assert": [{"clipboard": {"equals": "COUPON123"}}]}]}
    )
    result = run_scenario(FakeDriver(), scn, control=ctrl)
    assert not result.ok
    assert "clipboard" in (result.failure or "")


def test_clipboard_assertion_without_control_fails_cleanly() -> None:
    # No device-control channel (fake driver / parallel run): a clean failure, not a crash.
    scn = Scenario.model_validate(
        {"name": "s", "steps": [{"assert": [{"clipboard": {"equals": "x"}}]}]}
    )
    result = run_scenario(FakeDriver(), scn)
    assert not result.ok
    assert "clipboard" in (result.failure or "")


def test_clipboard_assertion_read_failure_fails_cleanly() -> None:
    # A pbpaste failure (simctl errored) must fail the assertion cleanly, not abort the run.
    import subprocess

    class _FailingClipboard(_RecordingControl):
        def get_clipboard(self) -> str:
            raise subprocess.CalledProcessError(72, ["xcrun", "simctl", "pbpaste"])

    scn = Scenario.model_validate(
        {"name": "s", "steps": [{"assert": [{"clipboard": {"equals": "x"}}]}]}
    )
    result = run_scenario(FakeDriver(), scn, control=_FailingClipboard())
    assert not result.ok
    assert "clipboard" in (result.failure or "")


def test_clipboard_expect_retry_rereads_after_on_blocked() -> None:
    # When on_blocked clears a block and the app then updates the pasteboard, the expect retry must
    # compare against the fresh clipboard, not the stale pre-block value.
    from conftest import el

    from bajutsu.drivers import base
    from bajutsu.orchestrator.types import AlertEvent

    ctrl = _RecordingControl()
    ctrl.clipboard_value = "STALE"

    def on_blocked(_driver: base.Driver) -> AlertEvent:
        ctrl.clipboard_value = "COUPON123"  # the cleared block let the app write the pasteboard
        return AlertEvent(label="Not Now")

    scn = Scenario.model_validate(
        {
            "name": "s",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"clipboard": {"equals": "COUPON123"}}],
        }
    )
    result = run_scenario(
        FakeDriver([el("a", "A", ["button"])]),
        scn,
        control=ctrl,
        alert_guard=AlertGuardConfig(vision=on_blocked),
    )
    assert result.ok  # first read STALE failed, on_blocked fired, re-read COUPON123 passed


def test_set_clipboard_without_control_fails_cleanly() -> None:
    scn = Scenario(name="s", steps=[Step(set_clipboard=SetClipboard(text="x"))])
    result = run_scenario(FakeDriver(), scn)
    assert not result.ok
    assert "setClipboard" in (result.failure or "")


def test_foreground_without_control_fails_cleanly() -> None:
    scn = Scenario(name="s", steps=[Step(foreground=Foreground())])
    result = run_scenario(FakeDriver(), scn)
    assert not result.ok
    assert "foreground" in (result.failure or "")
