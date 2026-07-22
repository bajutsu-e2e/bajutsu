"""Tests for the adb command layer and the Android environment (BE-0007 Unit 7, fast gate).

The command builders are pure; `Env` and `AndroidEnvironment` run through an injected `run`, so the
whole launch sequence — boot wait → install → pm clear → force-stop → am start → deeplink — is
asserted without a device, the Android peer of `test_simctl.py`.
"""

from __future__ import annotations

import pytest

from bajutsu import adb
from bajutsu.config import AndroidConfig, Effective
from bajutsu.drivers.adb import AdbDriver
from bajutsu.platform_lifecycle import AndroidEnvironment, ProvisionProfile, environment_for
from bajutsu.scenario import Preconditions, Redact


def test_bad_serial_is_rejected() -> None:
    # A serial from --udid / config that could inject an adb option (leading `-`) or a shell
    # metacharacter is rejected before it reaches a subprocess argv.
    for bad in ["-rf", "a b", "a;b", "a$b", ""]:
        with pytest.raises(adb.DeviceError, match="invalid device serial"):
            adb.tap_cmd(bad, 1, 2)
    # A normal emulator / device serial passes through.
    assert adb.tap_cmd("emulator-5554", 1, 2)[:3] == ["adb", "-s", "emulator-5554"]


def test_driver_rejects_bad_serial_at_construction() -> None:
    # AdbDriver validates the serial in __init__, so a malicious id fails fast at construction
    # rather than lying dormant until the first command builder runs.
    with pytest.raises(adb.DeviceError, match="invalid device serial"):
        AdbDriver("bad;rm")
    # A normal serial constructs fine and is stored verbatim.
    assert AdbDriver("emulator-5554").serial == "emulator-5554"


def test_env_rejects_bad_serial_at_construction() -> None:
    # Env is the object AndroidEnvironment.start drives for the real device-lifecycle path, so
    # it validates the serial at construction too — the same guard, at the same object boundary.
    with pytest.raises(adb.DeviceError, match="invalid device serial"):
        adb.Env("bad;rm")
    assert adb.Env("emulator-5554").serial == "emulator-5554"


def test_command_builders() -> None:
    assert adb.dump_cmd("S") == ["adb", "-s", "S", "exec-out", "uiautomator", "dump", "/dev/tty"]
    assert adb.tap_cmd("S", 12.6, 20.4) == ["adb", "-s", "S", "shell", "input", "tap", "13", "20"]
    assert adb.pm_clear_cmd("S", "com.x") == ["adb", "-s", "S", "shell", "pm", "clear", "com.x"]
    assert adb.install_cmd("S", "a.apk") == ["adb", "-s", "S", "install", "-r", "-t", "a.apk"]
    assert adb.keyevent_cmd("S", adb.KEYCODE_BACK) == [
        "adb", "-s", "S", "shell", "input", "keyevent", "4",
    ]  # fmt: skip
    assert adb.double_tap_cmd("S", 12.6, 20.4) == [
        "adb", "-s", "S", "shell", "input", "tap", "13", "20", ";", "input", "tap", "13", "20",
    ]  # fmt: skip


def test_evidence_command_builders() -> None:
    assert adb.screenrecord_cmd("S") == [
        "adb", "-s", "S", "shell", "screenrecord", adb.VIDEO_DEVICE_PATH,
    ]  # fmt: skip
    assert adb.screenrecord_cmd("S", "/sdcard/x.mp4")[-1] == "/sdcard/x.mp4"
    assert adb.logcat_cmd("S") == ["adb", "-s", "S", "logcat", "-T", "1"]
    assert adb.pull_cmd("S", "/sdcard/x.mp4", "/tmp/x.mp4") == [
        "adb", "-s", "S", "pull", "/sdcard/x.mp4", "/tmp/x.mp4",
    ]  # fmt: skip
    assert adb.rm_cmd("S", "/sdcard/x.mp4") == [
        "adb",
        "-s",
        "S",
        "shell",
        "rm",
        "-f",
        "/sdcard/x.mp4",
    ]


def test_pm_grant_cmd() -> None:
    assert adb.pm_grant_cmd("S", "com.x", "android.permission.CAMERA") == [
        "adb", "-s", "S", "shell", "pm", "grant", "com.x", "android.permission.CAMERA",
    ]  # fmt: skip


def test_env_grants_each_permission_in_order() -> None:
    calls: list[list[str]] = []
    adb.Env("S", run=lambda a: calls.append(a) or "").grant_permissions(
        "com.x", ["android.permission.CAMERA", "android.permission.POST_NOTIFICATIONS"]
    )
    assert calls == [
        adb.pm_grant_cmd("S", "com.x", "android.permission.CAMERA"),
        adb.pm_grant_cmd("S", "com.x", "android.permission.POST_NOTIFICATIONS"),
    ]


def test_grant_permissions_surfaces_a_stdout_error() -> None:
    # `pm grant` exits 0 even for an unknown permission, printing the error to stdout; a silently
    # swallowed grant would surface only as a later misleading failure, so any stdout fails loudly.
    def run(_a: list[str]) -> str:
        return "java.lang.IllegalArgumentException: Unknown permission: android.permission.BOGUS\n"

    with pytest.raises(adb.DeviceError, match="pm grant failed"):
        adb.Env("S", run=run).grant_permissions("com.x", ["android.permission.BOGUS"])


def test_pm_revoke_cmd() -> None:
    assert adb.pm_revoke_cmd("S", "com.x", "android.permission.CAMERA") == [
        "adb", "-s", "S", "shell", "pm", "revoke", "com.x", "android.permission.CAMERA",
    ]  # fmt: skip


def test_env_apply_permissions_grants_and_revokes_by_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # location splits into two android.permission.* names (fine + coarse); each runs its own
    # pm grant/revoke, in the mapped order (BE-0276).
    calls: list[list[str]] = []
    adb.Env("S", run=lambda a: calls.append(a) or "").apply_permissions(
        "com.x", {"location": "grant", "camera": "revoke"}
    )
    assert calls == [
        adb.pm_grant_cmd("S", "com.x", "android.permission.ACCESS_FINE_LOCATION"),
        adb.pm_grant_cmd("S", "com.x", "android.permission.ACCESS_COARSE_LOCATION"),
        adb.pm_revoke_cmd("S", "com.x", "android.permission.CAMERA"),
    ]


def test_env_apply_permissions_surfaces_a_stdout_error() -> None:
    def run(_a: list[str]) -> str:
        return "java.lang.SecurityException: not a changeable permission\n"

    with pytest.raises(adb.DeviceError, match="pm grant failed"):
        adb.Env("S", run=run).apply_permissions("com.x", {"camera": "grant"})


def test_env_apply_permissions_fails_loudly_on_an_unknown_action() -> None:
    # `Scenario.permissions` already validates grant|revoke, so this can't happen through a
    # scenario — but `_pm_run` must not silently treat anything-but-grant as a revoke.
    calls: list[list[str]] = []
    with pytest.raises(adb.DeviceError, match="unknown pm action"):
        adb.Env("S", run=lambda a: calls.append(a) or "").apply_permissions(
            "com.x", {"camera": "bogus"}
        )
    assert calls == []


def test_env_apply_permissions_validates_before_touching_the_device() -> None:
    # An unmapped service anywhere in the mapping fails before any pm call runs — never partway
    # through, leaving some services already mutated (BE-0276). Preflight rejects an unknown
    # service before it ever reaches here; this exercises the runtime backstop directly.
    calls: list[list[str]] = []
    with pytest.raises(adb.DeviceError, match="bogus"):
        adb.Env("S", run=lambda a: calls.append(a) or "").apply_permissions(
            "com.x", {"camera": "grant", "bogus": "grant"}
        )
    assert calls == []


def test_env_apply_permissions_validates_action_before_touching_the_device() -> None:
    # An unrecognized action anywhere in the mapping must also fail before any pm call runs — not
    # just an unmapped service — so an earlier, otherwise-valid entry is never mutated ahead of a
    # later entry's bad action (BE-0276).
    calls: list[list[str]] = []
    with pytest.raises(adb.DeviceError, match="unknown pm action"):
        adb.Env("S", run=lambda a: calls.append(a) or "").apply_permissions(
            "com.x", {"camera": "grant", "microphone": "bogus"}
        )
    assert calls == []


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
    *,
    package: str = "com.bajutsu.showcase.android.compose",
    app_path: str | None = None,
    grant_permissions: list[str] | None = None,
) -> Effective:
    # The Android platform sub-config (BE-0126) carries package / app_path / grant_permissions.
    return Effective(
        target="showcase-compose",
        platform_config=AndroidConfig(
            package=package, app_path=app_path, grant_permissions=grant_permissions or []
        ),
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


def test_android_environment_starts_screenrecord_before_launching_the_app(tmp_path) -> None:
    # The video must begin before `am start` so the app's cold start is recorded; the running
    # screenrecord is exposed for the sink to adopt rather than started on demand after launch.
    events: list[str] = []

    def run(args: list[str]) -> str:
        if "sys.boot_completed" in args:
            return "1\n"
        if "resolve-activity" in args:
            return "com.bajutsu.showcase.android.compose/.MainActivity\n"
        if "am start" in " ".join(args):
            events.append("launch")
        return ""

    class _Proc:
        def stop(self, sig: int, timeout: float) -> None:
            pass

    def spawn(argv: list[str], stdout_path: object) -> _Proc:
        events.append("record")
        return _Proc()

    env = AndroidEnvironment("adb", "emulator-5554", adb_run=run, spawn=spawn)  # type: ignore[arg-type]
    env.start(_eff(), Preconditions(), record_video_dir=tmp_path)

    assert events == ["record", "launch"]  # recording began before the app launched
    started = env.prestarted_intervals()
    assert len(started) == 1 and started[0].kind == "video"


def test_android_environment_start_grants_configured_permissions_after_clear() -> None:
    # BE-0210: runtime permissions are granted up front (`pm grant`) so a permission prompt never
    # blocks the run — deterministic, no timing on the run path. It must run AFTER `pm clear` (which
    # resets granted permissions) so the grants survive the clean-state reset.
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(
        _eff(grant_permissions=["android.permission.POST_NOTIFICATIONS"]),
        Preconditions(erase=True),
    )
    joined = [" ".join(c) for c in calls]
    assert any("pm grant com.bajutsu.showcase.android.compose "
               "android.permission.POST_NOTIFICATIONS" in j for j in joined)  # fmt: skip
    clear_at = next(i for i, j in enumerate(joined) if "pm clear" in j)
    grant_at = next(i for i, j in enumerate(joined) if "pm grant" in j)
    assert grant_at > clear_at  # granted after the clean-state reset, so the grant is not wiped


def test_android_environment_start_grants_nothing_when_unconfigured() -> None:
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions())
    assert not any("pm grant" in " ".join(c) for c in calls)


def test_android_environment_start_applies_scenario_permissions_after_clear() -> None:
    # BE-0276: the per-scenario field layers on top of the config-level grant above, applied the
    # same way — after `pm clear` (which resets grants), before `am start`.
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(
        _eff(grant_permissions=["android.permission.POST_NOTIFICATIONS"]),
        Preconditions(erase=True),
        permissions={"camera": "grant", "location": "revoke"},
    )
    joined = [" ".join(c) for c in calls]
    assert any("pm grant com.bajutsu.showcase.android.compose android.permission.CAMERA" in j for j in joined)  # fmt: skip
    assert any("pm revoke com.bajutsu.showcase.android.compose "
               "android.permission.ACCESS_FINE_LOCATION" in j for j in joined)  # fmt: skip
    clear_at = next(i for i, j in enumerate(joined) if "pm clear" in j)
    launch_at = next(i for i, j in enumerate(joined) if "am start" in j)
    permission_indices = [i for i, j in enumerate(joined) if "pm grant" in j or "pm revoke" in j]
    assert all(clear_at < i < launch_at for i in permission_indices)


def test_android_environment_start_applies_no_permissions_when_unset() -> None:
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions())
    assert not any("pm revoke" in " ".join(c) for c in calls)


class _FakeResident:
    """A resident server stand-in: start() hands back a fetch; stop() records the teardown."""

    def __init__(self, *, fetch: object = None, error: Exception | None = None) -> None:
        self._fetch = fetch
        self._error = error
        self.stopped = False

    def start(self):  # type: ignore[no-untyped-def]
        if self._error is not None:
            raise self._error
        return self._fetch

    def stop(self) -> None:
        self.stopped = True


def test_android_environment_wires_the_resident_channel_when_enabled() -> None:
    # With the resident server available, the returned driver reads over its fetch (BE-0245) instead
    # of `uiautomator dump`, and teardown stops the server so no instrumentation is left running.
    xml = (
        "<?xml version='1.0' ?><hierarchy rotation=\"0\">"
        '<node index="0" class="android.widget.Button" resource-id="stable.submit" '
        'text="送信" bounds="[0,0][10,10]" /></hierarchy>'
    )
    resident = _FakeResident(fetch=lambda: xml)

    def run(args: list[str]) -> str:
        raise AssertionError(f"resident read must not shell out: {args}")

    env = AndroidEnvironment(
        "adb",
        "S",
        adb_run=_resolve_activity_run([]),
        resident_factory=lambda: resident,
    )
    driver = env.start(_eff(), Preconditions())
    driver._run = run  # type: ignore[attr-defined]  # prove the read never reaches the dump subprocess
    assert len(driver.query()) == 1  # read came from the resident fetch

    env.teardown(driver, _eff())
    assert resident.stopped


def test_android_environment_falls_back_to_dump_when_resident_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A resident startup failure is not fatal: start still returns a working adb driver (reading via
    # `uiautomator dump`), logs the degradation loudly, and leaves nothing to tear down.
    from bajutsu.drivers.adb import AdbResidentError

    resident = _FakeResident(error=AdbResidentError("not built"))
    env = AndroidEnvironment(
        "adb",
        "S",
        adb_run=_resolve_activity_run([]),
        resident_factory=lambda: resident,
    )
    import logging

    with caplog.at_level(logging.WARNING):
        driver = env.start(_eff(), Preconditions())
    assert driver.name == "adb"
    assert any("resident" in r.message.lower() for r in caplog.records)
    env.teardown(driver, _eff())
    assert not resident.stopped  # it never started, so nothing to stop


def test_android_environment_skips_resident_when_the_server_is_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default-on is gated on the server being built (BE-0245 PR-D): with no factory, the env
    # override unset, and the APKs absent, start reads via `uiautomator dump` — a fresh clone that
    # never ran `make -C BajutsuAndroidUIAutomatorServer build` is never worse off than before.
    import bajutsu.adb_resident as adb_resident

    monkeypatch.delenv("BAJUTSU_ADB_RESIDENT", raising=False)
    monkeypatch.setattr(adb_resident, "server_apks_built", lambda *a: False)
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run([]))
    env.start(_eff(), Preconditions())
    assert env._resident is None  # nothing started


def test_make_resident_defaults_on_when_the_server_apks_are_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default (env unset) now routes reads through the resident channel automatically once the
    # server is built — no opt-in flag needed. Construction only stores params (no device contact).
    import bajutsu.adb_resident as adb_resident

    monkeypatch.delenv("BAJUTSU_ADB_RESIDENT", raising=False)
    monkeypatch.setattr(adb_resident, "server_apks_built", lambda *a: True)
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run([]))
    assert isinstance(env._make_resident(), adb_resident.ResidentServer)


def test_make_resident_env_off_opts_out_even_when_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit BAJUTSU_ADB_RESIDENT=0 forces the dump path even on a built tree — the escape hatch
    # for pinning the fallback path (the Android e2e lane uses it to guard the dump path, PR-D).
    import bajutsu.adb_resident as adb_resident

    monkeypatch.setenv("BAJUTSU_ADB_RESIDENT", "0")
    monkeypatch.setattr(adb_resident, "server_apks_built", lambda *a: True)
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run([]))
    assert env._make_resident() is None


def test_make_resident_env_on_forces_a_server_even_when_not_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit truthy override forces the resident channel even before a build; start() then
    # degrades loudly to dump if the APKs are missing (tested in test_adb_resident). Construction
    # only stores params, so this stays hermetic. (monkeypatch auto-restores the env var.)
    import bajutsu.adb_resident as adb_resident

    monkeypatch.setenv("BAJUTSU_ADB_RESIDENT", "1")
    monkeypatch.setattr(adb_resident, "server_apks_built", lambda *a: False)
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run([]))
    assert isinstance(env._make_resident(), adb_resident.ResidentServer)


def test_android_environment_grants_permissions_even_on_overwrite() -> None:
    # An `overwrite` reinstall skips `pm clear` (keeps app data), but configured permissions must
    # still be granted — a kept-data app can still need a permission the run relies on.
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(
        _eff(grant_permissions=["android.permission.CAMERA"]),
        Preconditions(reinstall="overwrite"),
    )
    joined = [" ".join(c) for c in calls]
    assert not any("pm clear" in j for j in joined)  # overwrite keeps data
    assert any("pm grant com.bajutsu.showcase.android.compose android.permission.CAMERA" in j
               for j in joined)  # fmt: skip


def test_android_environment_start_forwards_collector_env_for_network_mocks() -> None:
    # Android observes no network natively, so it reuses iOS's mocked story: the pool passes the
    # app-side collector URL/token in extra_env, and start forwards them as intent extras the app
    # reads — the same wiring as the device backend, no new code path.
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    # The pool sets both BAJUTSU_COLLECTOR and BAJUTSU_COLLECTOR_TOKEN in extra_env; both ride the
    # same launch_env merge and must reach the app as intent extras.
    env.start(
        _eff(),
        Preconditions(),
        extra_env={"BAJUTSU_COLLECTOR": "http://127.0.0.1:9/", "BAJUTSU_COLLECTOR_TOKEN": "tok"},
    )
    joined = [" ".join(c) for c in calls]
    assert any("--es BAJUTSU_COLLECTOR http://127.0.0.1:9/" in j for j in joined)
    assert any("--es BAJUTSU_COLLECTOR_TOKEN tok" in j for j in joined)


def test_android_environment_bridge_collector_reverses_and_tears_down() -> None:
    # BE-0283: the emulator's 127.0.0.1 is its own loopback, so the host collector is reached via
    # `adb reverse tcp:<port> tcp:<port>`; the returned thunk removes the tunnel at lease end.
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    remove = env.bridge_collector(41000)
    assert ["adb", "-s", "S", "reverse", "tcp:41000", "tcp:41000"] in calls
    calls.clear()
    remove()
    assert ["adb", "-s", "S", "reverse", "--remove", "tcp:41000"] in calls


def test_android_environment_bridge_collector_teardown_swallows_adb_failure() -> None:
    # A device already gone at release makes `adb reverse --remove` fail; the teardown must swallow it
    # so the lease's own outcome is never masked (the tunnel dies with the emulator regardless).
    import subprocess

    def run(args: list[str]) -> str:
        if "--remove" in args:
            raise subprocess.CalledProcessError(1, args)
        return ""

    remove = AndroidEnvironment("adb", "S", adb_run=run).bridge_collector(41000)
    remove()  # does not raise


def test_android_environment_bridge_collector_setup_failure_raises_clean_device_error() -> None:
    # A failing `adb reverse` at setup (device gone, too many tunnels) must surface as a clean
    # DeviceError like every other adb call in the lease path, not a raw CalledProcessError out of
    # lease(). Mirrors start()'s CalledProcessError conversion.
    import subprocess

    def run(args: list[str]) -> str:
        if "reverse" in args:
            raise subprocess.CalledProcessError(1, args, stderr="error: no devices/emulators found")
        return ""

    with pytest.raises(adb.DeviceError, match="no devices/emulators found"):
        AndroidEnvironment("adb", "S", adb_run=run).bridge_collector(41000)


def test_android_environment_bridge_collector_raises_clean_device_error_when_adb_is_missing() -> (
    None
):
    # A missing `adb` binary makes the runner raise FileNotFoundError; bridge_collector converts it to
    # a clean DeviceError, mirroring start()'s missing-adb path.
    def no_adb(args: list[str]) -> str:
        raise FileNotFoundError("adb")

    with pytest.raises(adb.DeviceError, match="adb"):
        AndroidEnvironment("adb", "S", adb_run=no_adb).bridge_collector(41000)


def test_android_environment_start_skips_pm_clear_on_overwrite() -> None:
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions(reinstall="overwrite"))
    assert not any("pm clear" in " ".join(c) for c in calls)  # overwrite keeps app data


def test_android_environment_start_skips_boot_wait_when_provisioned() -> None:
    # A device provider that reports the device already booted (a cloud device handed over ready)
    # lets start() skip the boot-readiness poll — the ProvisionProfile the lease carries, threaded to
    # the environment (BE-0236). A locally-attached device leaves boot_ready False, so the poll runs.
    calls: list[list[str]] = []
    env = AndroidEnvironment(
        "adb",
        "S",
        adb_run=_resolve_activity_run(calls),
        provision=ProvisionProfile(boot_ready=True),
    )
    env.start(_eff(), Preconditions())
    assert not any("getprop sys.boot_completed" in " ".join(c) for c in calls)


def test_android_environment_start_skips_install_when_preinstalled() -> None:
    # A provider that reports the app already installed lets start() skip the install step, so a
    # nonexistent local appPath is never even probed (a cloud device already carries the build).
    calls: list[list[str]] = []
    env = AndroidEnvironment(
        "adb",
        "S",
        adb_run=_resolve_activity_run(calls),
        provision=ProvisionProfile(app_preinstalled=True),
    )
    env.start(_eff(app_path="/nonexistent/app.apk"), Preconditions())  # would raise if not skipped
    assert not any("install" in " ".join(c) for c in calls)


def test_android_environment_preinstalled_skip_still_fails_loudly_on_a_missing_app() -> None:
    # The install-skip trusts the provider's app_preinstalled claim, but a genuinely-absent app is
    # not silently tolerated: with nothing installed and no launcher activity, `am start` →
    # resolve_activity raises a clean DeviceError (BE-0236). The skip never degrades to a silent pass.
    def run(args: list[str]) -> str:
        if "sys.boot_completed" in args:
            return "1\n"
        if "resolve-activity" in args:
            return "No activity found\n"  # the app is not actually on the device
        return ""

    env = AndroidEnvironment(
        "adb", "S", adb_run=run, provision=ProvisionProfile(app_preinstalled=True)
    )
    with pytest.raises(adb.DeviceError, match="no launcher activity"):
        env.start(_eff(app_path="/nonexistent/app.apk"), Preconditions())


def test_android_environment_boot_ready_skip_still_fails_loudly_on_a_not_booted_device() -> None:
    # The boot-wait skip trusts the provider's boot_ready claim, but a device that is not actually
    # booted is not silently tolerated: with the boot poll skipped, the very next real adb call (here
    # `pm clear`, driven by `erase`) hits the not-ready device and errors, which `start` converts to a
    # clean DeviceError (BE-0236). The skip never degrades to a silent pass. Symmetric to the
    # app_preinstalled false-claim test above, covering the other half of the loud-failure comment.
    import subprocess

    def run(args: list[str]) -> str:
        if args == adb.pm_clear_cmd("S", "com.bajutsu.showcase.android.compose"):
            # A not-booted device rejects the command adb tries to run on it.
            raise subprocess.CalledProcessError(1, args, stderr="error: device offline")
        if "resolve-activity" in args:
            return "com.bajutsu.showcase.android.compose/.MainActivity\n"
        return ""

    env = AndroidEnvironment("adb", "S", adb_run=run, provision=ProvisionProfile(boot_ready=True))
    with pytest.raises(adb.DeviceError, match="device offline"):
        env.start(_eff(), Preconditions(erase=True))


def test_android_environment_default_profile_boots_and_polls() -> None:
    # The inert default profile (a locally-attached device) preserves today's sequence: the boot poll
    # runs. The skip is opt-in via the provider; nothing else about start() changes (BE-0236).
    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "S", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions())
    assert any("getprop sys.boot_completed" in " ".join(c) for c in calls)


def test_environment_for_adb_returns_android_environment() -> None:
    assert isinstance(environment_for("adb", "emulator-5554"), AndroidEnvironment)


def test_environment_for_threads_the_provision_profile() -> None:
    # `environment_for` carries the run's ProvisionProfile onto the Android environment, so the pool's
    # lease-time construction preserves the provider's readiness report (BE-0236).
    env = environment_for("adb", "S", provision=ProvisionProfile(boot_ready=True))
    assert isinstance(env, AndroidEnvironment)
    assert env._provision == ProvisionProfile(boot_ready=True)


def test_device_error_keeps_command_and_stderr() -> None:
    import subprocess

    exc = subprocess.CalledProcessError(1, ["adb", "install", "x.apk"], stderr="Failure [INSTALL]")
    err = adb.device_error(exc)
    assert "exit 1" in str(err) and "Failure [INSTALL]" in str(err)


def test_device_error_decodes_bytes_stderr() -> None:
    import subprocess

    # A text=False caller yields bytes stderr; it must be decoded, not dropped (the message is the
    # most actionable part of the failure).
    exc = subprocess.CalledProcessError(1, ["adb", "install", "x.apk"], stderr=b"Failure [INSTALL]")
    assert "Failure [INSTALL]" in str(adb.device_error(exc))


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
