"""Tests for the simctl command layer (builders + injectable runner)."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping

from bajutsu import env


def test_command_builders() -> None:
    assert env.erase_cmd("U") == ["xcrun", "simctl", "erase", "U"]
    assert env.boot_cmd("U") == ["xcrun", "simctl", "boot", "U"]
    assert env.openurl_cmd("U", "app://x") == ["xcrun", "simctl", "openurl", "U", "app://x"]
    assert env.screenshot_cmd("U", "/p.png") == ["xcrun", "simctl", "io", "U", "screenshot", "/p.png"]
    assert env.launch_cmd("U", "com.x", ["-flag", "1"]) == [
        "xcrun", "simctl", "launch", "--terminate-running-process", "U", "com.x", "-flag", "1",
    ]


def test_booted_udids_parses_simctl() -> None:
    import json

    payload = json.dumps({
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                {"udid": "AAA", "state": "Booted"},
                {"udid": "BBB", "state": "Shutdown"},
            ],
        }
    })
    assert env.booted_udids(run=lambda args, e=None: payload) == ["AAA"]

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert env.booted_udids(run=boom) == []  # failure -> empty, never raises


def test_locale_args() -> None:
    assert env.locale_args("ja_JP") == ["-AppleLocale", "ja_JP", "-AppleLanguages", "(ja)"]
    assert env.locale_args("en") == ["-AppleLocale", "en", "-AppleLanguages", "(en)"]


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


def test_shutdown_is_idempotent() -> None:
    """shutdown() of an already-shut-down device is a no-op, not a crash."""
    calls: list[list[str]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append(args)
        raise subprocess.CalledProcessError(
            1, args, stderr="Unable to shutdown device in current state: Shutdown"
        )

    env.Env("UDID", run=fake_run).shutdown()  # swallows the error
    assert calls == [["xcrun", "simctl", "shutdown", "UDID"]]


def test_device_error_keeps_command_and_simctl_stderr() -> None:
    exc = subprocess.CalledProcessError(
        149,
        ["xcrun", "simctl", "erase", "U"],
        output="",
        stderr="Unable to erase contents and settings in current state: Booted\n",
    )
    err = env.device_error(exc)
    assert isinstance(err, env.DeviceError)
    msg = str(err)
    assert "exit 149" in msg
    assert "xcrun simctl erase U" in msg
    assert "Booted" in msg  # simctl's own (actionable) stderr is preserved
