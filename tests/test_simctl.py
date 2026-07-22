"""Tests for the simctl command layer (builders + injectable runner)."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping

import pytest

from bajutsu import simctl


def test_command_builders() -> None:
    assert simctl.erase_cmd("U") == ["xcrun", "simctl", "erase", "U"]
    assert simctl.boot_cmd("U") == ["xcrun", "simctl", "boot", "U"]
    assert simctl.openurl_cmd("U", "app://x") == ["xcrun", "simctl", "openurl", "U", "app://x"]
    assert simctl.screenshot_cmd("U", "/p.png") == [
        "xcrun",
        "simctl",
        "io",
        "U",
        "screenshot",
        "/p.png",
    ]
    assert simctl.launch_cmd("U", "com.x", ["-flag", "1"]) == [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        "U",
        "com.x",
        "-flag",
        "1",
    ]
    assert simctl.list_devices_cmd() == ["xcrun", "simctl", "list", "devices", "available", "-j"]
    assert simctl.bootstatus_cmd("U") == ["xcrun", "simctl", "bootstatus", "U", "-b"]
    assert simctl.install_cmd("U", "/p.app") == ["xcrun", "simctl", "install", "U", "/p.app"]
    assert simctl.uninstall_cmd("U", "com.x") == ["xcrun", "simctl", "uninstall", "U", "com.x"]
    assert simctl.get_app_container_cmd("U", "com.x") == [
        "xcrun",
        "simctl",
        "get_app_container",
        "U",
        "com.x",
        "app",
    ]


def test_uninstall_is_idempotent() -> None:
    """uninstall() of an app that isn't installed is a no-op, not a crash."""

    def absent(args: list[str], e: object = None) -> str:
        raise subprocess.CalledProcessError(2, args, stderr="not installed")

    simctl.Env("U", run=absent).uninstall("com.x")  # swallows the error


def test_is_installed_reflects_get_app_container() -> None:
    import subprocess

    def present(args: list[str], e: object = None) -> str:
        return "/path/to.app"

    def absent(args: list[str], e: object = None) -> str:
        raise subprocess.CalledProcessError(2, args, stderr="No such file or directory")

    assert simctl.Env("U", run=present).is_installed("com.x") is True
    assert simctl.Env("U", run=absent).is_installed("com.x") is False  # missing -> False, no raise


def test_booted_udids_parses_simctl() -> None:
    import json

    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [
                    {"udid": "AAA", "state": "Booted"},
                    {"udid": "BBB", "state": "Shutdown"},
                ],
            }
        }
    )
    assert simctl.booted_udids(run=lambda args, e=None: payload) == ["AAA"]

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert simctl.booted_udids(run=boom) == []  # failure -> empty, never raises


def test_runtime_label_humanizes_identifier() -> None:
    assert simctl.runtime_label("com.apple.CoreSimulator.SimRuntime.iOS-26-5") == "iOS 26.5"
    assert simctl.runtime_label("com.apple.CoreSimulator.SimRuntime.watchOS-11-0") == "watchOS 11.0"


def test_device_catalog_maps_udid_to_model_and_os() -> None:
    import json

    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-17-2": [
                    {"udid": "AAA", "name": "iPhone 15", "isAvailable": True},
                    {"name": "no-udid-skipped"},
                ],
            }
        }
    )
    catalog = simctl.device_catalog(run=lambda args, e=None: payload)
    assert catalog == {"AAA": {"name": "iPhone 15", "runtime": "iOS 17.2"}}

    def boom(args: list[str], e: object = None) -> str:
        raise OSError("simctl not found")

    assert simctl.device_catalog(run=boom) == {}  # failure -> empty, never raises


def test_locale_args() -> None:
    assert simctl.locale_args("ja_JP") == ["-AppleLocale", "ja_JP", "-AppleLanguages", "(ja)"]
    assert simctl.locale_args("en") == ["-AppleLocale", "en", "-AppleLanguages", "(en)"]


def test_child_env_prefix() -> None:
    assert simctl.child_env({"FOO": "1"}) == {"SIMCTL_CHILD_FOO": "1"}


def test_env_uses_injected_runner() -> None:
    calls: list[tuple[list[str], Mapping[str, str] | None]] = []

    def fake_run(args: list[str], extra_env: Mapping[str, str] | None = None) -> str:
        calls.append((args, extra_env))
        return ""

    e = simctl.Env("UDID", run=fake_run)
    e.erase()
    e.launch("com.x", ["-a"], {"K": "v"})
    e.openurl("app://settings")

    assert calls[0] == (["xcrun", "simctl", "erase", "UDID"], None)
    assert calls[1][0] == [
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        "UDID",
        "com.x",
        "-a",
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

    simctl.Env("UDID", run=fake_run).shutdown()  # swallows the error
    assert calls == [["xcrun", "simctl", "shutdown", "UDID"]]


def test_command_builders_reject_unvalidated_udid() -> None:
    # Each builder validates the udid inline, so a direct builder call (bypassing Env, as
    # serve does with bootstatus_cmd) can't smuggle an option-injecting / metacharacter id into
    # xcrun argv — the same guarantee every argv builder gives.
    for builder in (simctl.erase_cmd, simctl.boot_cmd, simctl.bootstatus_cmd, simctl.pbpaste_cmd):
        with pytest.raises(simctl.DeviceError, match="invalid udid"):
            builder("-rf; rm")
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        simctl.launch_cmd("--set", "com.x")


def test_env_validates_udid_at_construction() -> None:
    # Env validates once in __init__ against the shared device-id policy, so every self.udid argv
    # builder (erase/boot/launch/…) is covered — a malicious --udid can never reach a subprocess
    # argv, not just the hand-patched ones. A leading `-` (option injection) / shell metacharacter /
    # space / over-length id is rejected as a DeviceError, so the CLI exits 2 cleanly.
    for bad in ["-rf", "--set", "a b", "a;b", "a$b", "", "x" * 129]:
        with pytest.raises(simctl.DeviceError, match="invalid udid"):
            simctl.Env(bad)
    # UUID- / device-shaped ids and the `booted` alias pass through unchanged.
    for good in ["booted", "U", "A1B2C3D4-1122-3344-5566-77889900AABB"]:
        assert simctl.Env(good).udid == good


def test_device_error_keeps_command_and_simctl_stderr() -> None:
    exc = subprocess.CalledProcessError(
        149,
        ["xcrun", "simctl", "erase", "U"],
        output="",
        stderr="Unable to erase contents and settings in current state: Booted\n",
    )
    err = simctl.device_error(exc)
    assert isinstance(err, simctl.DeviceError)
    msg = str(err)
    assert "exit 149" in msg
    assert "xcrun simctl erase U" in msg
    assert "Booted" in msg  # simctl's own (actionable) stderr is preserved
