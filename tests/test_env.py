"""Tests for the simctl command layer (builders + injectable runner)."""

from __future__ import annotations

from collections.abc import Mapping

from simpilot import env


def test_command_builders() -> None:
    assert env.erase_cmd("U") == ["xcrun", "simctl", "erase", "U"]
    assert env.boot_cmd("U") == ["xcrun", "simctl", "boot", "U"]
    assert env.openurl_cmd("U", "app://x") == ["xcrun", "simctl", "openurl", "U", "app://x"]
    assert env.screenshot_cmd("U", "/p.png") == ["xcrun", "simctl", "io", "U", "screenshot", "/p.png"]
    assert env.launch_cmd("U", "com.x", ["-flag", "1"]) == [
        "xcrun", "simctl", "launch", "--terminate-running-process", "U", "com.x", "-flag", "1",
    ]


def test_child_env_prefix() -> None:
    assert env.child_env({"FOO": "1"}) == {"SIMCTL_CHILD_FOO": "1"}


def test_env_uses_injected_runner() -> None:
    calls: list[tuple[list[str], Mapping[str, str] | None]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append((args, extra_env))
        return ""

    e = env.Env("UDID", run=fake_run)
    e.erase()
    e.launch("com.x", ["-a"], {"K": "v"})
    e.openurl("app://settings")

    assert calls[0] == (["xcrun", "simctl", "erase", "UDID"], None)
    assert calls[1][0] == [
        "xcrun", "simctl", "launch", "--terminate-running-process", "UDID", "com.x", "-a",
    ]
    assert calls[1][1] == {"SIMCTL_CHILD_K": "v"}
    assert calls[2] == (["xcrun", "simctl", "openurl", "UDID", "app://settings"], None)
