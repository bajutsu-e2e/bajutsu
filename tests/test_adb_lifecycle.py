"""Tests for the adb command layer and the Android environment (BE-0007 Unit 7, fast gate).

The command builders are pure; `Env` and `AndroidEnvironment` run through an injected `run`, so the
whole launch sequence — boot wait → install → pm clear → force-stop → am start → deeplink — is
asserted without a device, the Android peer of `test_simctl.py`.
"""

from __future__ import annotations

import pytest

from bajutsu import adb
from bajutsu.config import AndroidConfig, Effective
from bajutsu.platform_lifecycle import AndroidEnvironment, environment_for
from bajutsu.scenario import Preconditions, Redact


def test_bad_serial_is_rejected() -> None:
    # A serial from --udid / config that could inject an adb option (leading `-`) or a shell
    # metacharacter is rejected before it reaches a subprocess argv.
    for bad in ["-rf", "a b", "a;b", "a$b", ""]:
        with pytest.raises(adb.DeviceError, match="invalid device serial"):
            adb.tap_cmd(bad, 1, 2)
    # A normal emulator / device serial passes through.
    assert adb.tap_cmd("emulator-5554", 1, 2)[:3] == ["adb", "-s", "emulator-5554"]


def test_command_builders() -> None:
    assert adb.dump_cmd("S") == ["adb", "-s", "S", "exec-out", "uiautomator", "dump", "/dev/tty"]
    assert adb.tap_cmd("S", 12.6, 20.4) == ["adb", "-s", "S", "shell", "input", "tap", "13", "20"]
    assert adb.pm_clear_cmd("S", "com.x") == ["adb", "-s", "S", "shell", "pm", "clear", "com.x"]
    assert adb.install_cmd("S", "a.apk") == ["adb", "-s", "S", "install", "-r", "-t", "a.apk"]


def test_launch_cmd_forwards_extras_as_intent_extras() -> None:
    cmd = adb.launch_cmd("S", "com.x/.Main", {"SHOWCASE_UITEST": "1"})
    assert cmd == [
        "adb", "-s", "S", "shell", "am", "start", "-W", "-n", "com.x/.Main",
        "--es", "SHOWCASE_UITEST", "1",
    ]  # fmt: skip


def test_deeplink_cmd_is_scoped_to_the_package() -> None:
    assert adb.deeplink_cmd("S", "showcasecompose://stable", "com.x") == [
        "adb", "-s", "S", "shell", "am", "start",
        "-a", "android.intent.action.VIEW", "-d", "showcasecompose://stable", "com.x",
    ]  # fmt: skip


def test_parse_devices_keeps_only_ready_devices() -> None:
    out = "List of devices attached\nemulator-5554\tdevice\nemulator-5556\toffline\n"
    assert adb._parse_devices(out) == ["emulator-5554"]


def test_resolve_serial_picks_first_ready_device() -> None:
    run = lambda a: "List of devices attached\nemulator-5554\tdevice\n"  # noqa: E731
    assert adb.resolve_serial("booted", run) == "emulator-5554"
    assert adb.resolve_serial("emulator-9999", run) == "emulator-9999"  # concrete passes through


def test_device_catalog_labels_model_and_release() -> None:
    def run(args: list[str]) -> str:
        if args == adb.devices_cmd():
            return "List of devices attached\nemulator-5554\tdevice\n"
        if "ro.product.model" in args:
            return "sdk_gphone64_arm64\n"
        if "ro.build.version.release" in args:
            return "14\n"
        return ""

    assert adb.device_catalog(run) == {
        "emulator-5554": {"name": "sdk_gphone64_arm64", "runtime": "Android 14"}
    }


def test_env_resolve_activity_parses_component() -> None:
    run = lambda a: "priority=0\n  com.x/.MainActivity\n"  # noqa: E731
    assert adb.Env("S", run=run).resolve_activity("com.x") == "com.x/.MainActivity"


def test_env_resolve_activity_raises_when_absent() -> None:
    with pytest.raises(adb.DeviceError, match="no launcher activity"):
        adb.Env("S", run=lambda a: "No activity found\n").resolve_activity("com.x")


def test_env_resolve_activity_ignores_a_stray_path_line() -> None:
    # A leading-slash path in the manager's chatter must not be mistaken for a `pkg/activity`
    # component (its left side of `/` is empty), so the real component on an earlier line wins.
    out = "com.x/.MainActivity\n/data/app/com.x/base.apk\n"
    assert adb.Env("S", run=lambda a: out).resolve_activity("com.x") == "com.x/.MainActivity"


def _eff(
    *, package: str = "com.bajutsu.showcase.android.compose", app_path: str | None = None
) -> Effective:
    # The Android platform sub-config (BE-0126) carries package / app_path.
    return Effective(
        target="showcase-compose",
        platform_config=AndroidConfig(package=package, app_path=app_path),
        backend=["android"],
        device="booted",
        locale="en_US",
        launch_env={"SHOWCASE_UITEST": "1"},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


def _resolve_activity_run(calls: list[list[str]]):
    """An adb `run` that records every argv and answers the two queries the launch makes."""

    def run(args: list[str]) -> str:
        calls.append(args)
        if "sys.boot_completed" in args:
            return "1\n"
        if "resolve-activity" in args:
            return "com.bajutsu.showcase.android.compose/.MainActivity\n"
        if args == adb.devices_cmd():
            return ""  # make_driver's AdbDriver only queries on demand; start does not query
        return ""

    return run


def test_android_environment_start_runs_the_adb_sequence() -> None:
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run(calls))
    pre = Preconditions(deeplink="showcasecompose://permissions")
    driver = env.start(_eff(), pre)

    assert driver.name == "adb"
    joined = [" ".join(c) for c in calls]
    # Boot readiness is polled (getprop), then clean state (pm clear), a clean start (force-stop),
    # launch (resolve-activity → am start with the launchEnv extra), then the deeplink — in order.
    assert any("getprop sys.boot_completed" in j for j in joined)
    order = [
        i
        for i, j in enumerate(joined)
        if any(k in j for k in ("pm clear", "force-stop", "am start -W -n", "action.VIEW"))
    ]
    assert order == sorted(order)  # the five steps fire in sequence
    assert any("pm clear com.bajutsu.showcase.android.compose" in j for j in joined)
    assert any("--es SHOWCASE_UITEST 1" in j for j in joined)
    assert any("action.VIEW -d showcasecompose://permissions" in j for j in joined)


def test_android_environment_start_skips_pm_clear_on_overwrite() -> None:
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions(reinstall="overwrite"))
    assert not any("pm clear" in " ".join(c) for c in calls)  # overwrite keeps app data


def test_environment_for_adb_returns_android_environment() -> None:
    assert isinstance(environment_for("adb", "emulator-5554"), AndroidEnvironment)


def test_device_error_keeps_command_and_stderr() -> None:
    import subprocess

    exc = subprocess.CalledProcessError(1, ["adb", "install", "x.apk"], stderr="Failure [INSTALL]")
    err = adb.device_error(exc)
    assert "exit 1" in str(err) and "Failure [INSTALL]" in str(err)


def test_booted_serials_empty_on_failure() -> None:
    import subprocess

    def boom(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args)

    assert adb.booted_serials(boom) == []
    assert adb.resolve_serial("booted", boom) == "booted"  # falls back, fails loudly later


def test_device_catalog_skips_a_device_whose_props_fail() -> None:
    import subprocess

    def run(args: list[str]) -> str:
        if args == adb.devices_cmd():
            return "emulator-5554\tdevice\n"
        raise subprocess.CalledProcessError(1, args)  # getprop fails

    assert adb.device_catalog(run) == {}


def test_start_raises_clean_device_error_when_adb_is_missing() -> None:
    # A missing `adb` binary makes the runner raise FileNotFoundError. `boot_completed` lets it
    # propagate (not a transient "not booted yet"), and `start` converts it to a clean DeviceError
    # so the CLI exits 2 instead of spinning to the boot deadline or dumping a traceback.
    def no_adb(args: list[str]) -> str:
        raise FileNotFoundError("adb")

    env = AndroidEnvironment("adb", "emulator-5554", adb_run=no_adb)
    with pytest.raises(adb.DeviceError, match="adb"):
        env.start(_eff(), Preconditions())
