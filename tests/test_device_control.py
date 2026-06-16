"""Tests for device control primitives (env simctl wrappers + setLocation/push steps)."""

from __future__ import annotations

import json

from bajutsu import env
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Push, Scenario, SetLocation, Step

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


# --- step dispatch through an injected DeviceControl ---


class _RecordingControl:
    def __init__(self) -> None:
        self.locations: list[tuple[float, float]] = []
        self.pushes: list[dict[str, object]] = []

    def set_location(self, lat: float, lon: float) -> None:
        self.locations.append((lat, lon))

    def push(self, payload: dict[str, object]) -> None:
        self.pushes.append(payload)

    def clear_keychain(self) -> None:
        pass

    def clear_clipboard(self) -> None:
        pass


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


def test_device_step_without_control_fails_cleanly() -> None:
    # No control injected (e.g. fake driver / parallel): the step fails, not crashes.
    scn = Scenario(name="s", steps=[Step(set_location=SetLocation(lat=1.0, lon=2.0))])
    result = run_scenario(FakeDriver(), scn)
    assert not result.ok
    assert "setLocation" in (result.failure or "")
