"""Tests for device control primitives (env simctl wrappers + setLocation/push steps)."""

from __future__ import annotations

import json

from bajutsu import env
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Foreground, Push, Scenario, SetClipboard, SetLocation, Step

# --- pure command builders ---


def test_location_and_push_command_builders() -> None:
    assert env.set_location_cmd("U", 35.6, 139.7) == [
        "xcrun",
        "simctl",
        "location",
        "U",
        "set",
        "35.6,139.7",
    ]
    assert env.clear_location_cmd("U") == ["xcrun", "simctl", "location", "U", "clear"]
    assert env.push_cmd("U", "com.demo", "/tmp/p.apns") == [
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

    env.Env("U", run=fake_run).push("com.demo", {"aps": {"alert": "hi"}})
    assert calls and calls[0][:5] == ["xcrun", "simctl", "push", "U", "com.demo"]
    assert written == {"aps": {"alert": "hi"}}


def test_env_set_location_runs_command() -> None:
    calls: list[list[str]] = []
    env.Env("U", run=lambda a, _e=None: calls.append(a) or "").set_location(1.0, 2.0)
    assert calls == [["xcrun", "simctl", "location", "U", "set", "1.0,2.0"]]


def test_foreground_command_builder() -> None:
    # resume a backgrounded app: launch WITHOUT --terminate (not a relaunch)
    assert env.foreground_cmd("U", "com.demo") == ["xcrun", "simctl", "launch", "U", "com.demo"]


def test_env_foreground_runs_command() -> None:
    calls: list[list[str]] = []
    env.Env("U", run=lambda a, _e=None: calls.append(a) or "").foreground("com.demo")
    assert calls == [["xcrun", "simctl", "launch", "U", "com.demo"]]


def test_env_set_clipboard_seeds_pasteboard_with_text(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict[str, object] = {}

    def fake_pbcopy(cmd: list[str], text: str = "") -> None:
        captured["cmd"], captured["text"] = cmd, text

    monkeypatch.setattr(env.Env, "_run_pbcopy", staticmethod(fake_pbcopy))
    env.Env("U").set_clipboard("COUPON123")
    assert captured["cmd"] == ["xcrun", "simctl", "pbcopy", "U"]
    assert captured["text"] == "COUPON123"


# --- step dispatch through an injected DeviceControl ---


class _RecordingControl:
    def __init__(self) -> None:
        self.locations: list[tuple[float, float]] = []
        self.pushes: list[dict[str, object]] = []
        self.home_calls: int = 0
        self.status_bar_overrides: list[dict[str, str | int]] = []
        self.clear_status_bar_calls: int = 0
        self.clipboards: list[str] = []
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
