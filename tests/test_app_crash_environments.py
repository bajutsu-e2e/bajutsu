"""The two environments' app-crash capture, and the launch markers it matches against (BE-0424).

The capture's correctness is almost entirely a question of *which* report it attaches, not whether it
finds one — so these tests are mostly about the launch marker. A marker stamped at the wrong moment,
or left stale across a relaunch, produces a capture that succeeds while naming the wrong crash, which
is a worse outcome than finding nothing: the directory exists specifically to attribute the report
correctly.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import el as _el

from bajutsu.common.config import AndroidConfig, Effective, IosConfig
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.platform_lifecycle import AndroidEnvironment
from bajutsu.common.platform_lifecycle.environments import ios as ios_environment
from bajutsu.common.platform_lifecycle.environments.android import android_environment
from bajutsu.common.platform_lifecycle.environments.xcuitest import xcuitest_environment
from bajutsu.common.platform_lifecycle.environments.xcuitest._functions import (
    _bundle_executable,
    _read_report,
    _reports_device,
)
from bajutsu.common.scenario import Preconditions, Redact

_UDID = "11111111-2222-3333-4444-555555555555"
_OTHER_UDID = "99999999-8888-7777-6666-555555555555"


def _ios_eff(app_path: str | None) -> Effective:
    return Effective(
        target="demo",
        platform_config=IosConfig(bundle_id="com.example.Showcase", app_path=app_path),
        backend=["xcuitest"],
        device="iPhone 15",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


def _android_eff() -> Effective:
    return Effective(
        target="demo",
        platform_config=AndroidConfig(package="com.example.app"),
        backend=["adb"],
        device="emulator-5554",
        locale="en_US",
        launch_env={},
        launch_args=[],
        id_namespaces=[],
        reserved_namespaces=[],
        mock_server=None,
        setup=None,
        capture=[],
        redact=Redact(),
    )


# --- iOS: the `.ips` sweep -------------------------------------------------------------------------


def _app_bundle(tmp_path: Path, executable: str = "Showcase") -> Path:
    """An installed `.app` whose `Info.plist` declares `CFBundleExecutable`."""
    import plistlib

    bundle = tmp_path / f"{executable}.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": executable}))
    return bundle


def _report(directory: Path, name: str, udid: str, *, mtime: float) -> Path:
    """An `.ips` file shaped like a real one: a header line, then a payload naming the install path."""
    path = directory / name
    path.write_text(
        '{"app_name":"Showcase","bundleID":"com.example.Showcase"}\n'
        '{"procPath":"/Users/ci/Library/Developer/CoreSimulator/Devices/'
        f'{udid}/data/Containers/Bundle/Application/ABC/Showcase.app/Showcase"}}\n'
    )
    import os

    os.utime(path, (mtime, mtime))
    return path


def _xcuitest_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reports: Path
) -> xcuitest_environment.XcuitestEnvironment:
    env = xcuitest_environment.XcuitestEnvironment("xcuitest", _UDID)
    monkeypatch.setattr(xcuitest_environment, "_diagnostic_reports_dir", lambda: reports)
    # A short bound: the sweep is a condition wait, so this changes only how long a *miss* takes.
    monkeypatch.setattr(xcuitest_environment, "_APP_CRASH_REPORT_TIMEOUT", 0.05)
    monkeypatch.setattr(xcuitest_environment, "_APP_CRASH_REPORT_POLL", 0.001)
    return env


def test_the_sweep_attaches_a_report_for_this_device_and_this_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 1000.0
    _report(reports, "Showcase-2026-09-16.ips", _UDID, mtime=1005.0)

    found = env.app_crash_artifacts()
    assert [name for name, _ in found] == ["Showcase-2026-09-16.ips"]


def test_the_sweep_rejects_another_simulators_report_for_the_same_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The case `--workers 2` on one CI host produces: two Simulators running the identical target
    # binary in the same window. No PID accessor exists on `XCUIApplication`, so the udid in the
    # report's own payload is what tells them apart.
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 1000.0
    _report(reports, "Showcase-2026-09-16.ips", _OTHER_UDID, mtime=1005.0)

    assert env.app_crash_artifacts() == []


def test_the_sweep_rejects_a_report_from_before_this_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole reason the marker is re-stamped at every launch site: a warm-reused lease would
    # otherwise match an earlier scenario's report, which passes both the executable-name and the
    # udid check, being the same app on the same Simulator.
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 2000.0
    _report(reports, "Showcase-2026-09-16.ips", _UDID, mtime=1005.0)

    assert env.app_crash_artifacts() == []


def test_a_target_with_no_app_path_resolves_to_nothing_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A named, up-front scope rather than an accidental miss: with no `appPath` there is no
    # `Info.plist` to read the executable name from, and no PID accessor to derive one instead — so
    # there is no fallback pattern to build.
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = None
    env._app_launched_at = 1000.0
    _report(reports, "Showcase-2026-09-16.ips", _UDID, mtime=1005.0)

    assert env.app_crash_artifacts() == []


def test_a_real_device_captures_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A real-device crash never lands in this host's own `DiagnosticReports`, so the capture is
    # scoped out alongside the driver's own signal.
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 1000.0
    env._is_real_device = True
    _report(reports, "Showcase-2026-09-16.ips", _UDID, mtime=1005.0)

    assert env.app_crash_artifacts() == []


def test_an_unreadable_report_store_resolves_to_nothing_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failure anywhere in the scan or the read is exactly as unable to change the app's own crash
    # verdict as a missing report is, so it resolves the same way.
    env = _xcuitest_env(tmp_path, monkeypatch, tmp_path / "does-not-exist")
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 1000.0

    assert env.app_crash_artifacts() == []


def test_an_exception_anywhere_in_the_sweep_resolves_to_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The outer `try`/`except Exception` around the whole body: not only an `OSError` from the
    # directory scan, but any failure at all, since none of them can change the app's own verdict.
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = 1000.0

    def explode(_app_path: str) -> str | None:
        raise ValueError("boom")

    monkeypatch.setattr(xcuitest_environment, "_bundle_executable", explode)
    assert env.app_crash_artifacts() == []


def test_no_launch_marker_resolves_to_nothing_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(_app_bundle(tmp_path))
    env._app_launched_at = None

    assert env.app_crash_artifacts() == []


def test_a_bundle_with_no_cf_bundle_executable_resolves_to_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import plistlib

    reports = tmp_path / "DiagnosticReports"
    reports.mkdir()
    bundle = tmp_path / "Bare.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_bytes(plistlib.dumps({}))
    env = _xcuitest_env(tmp_path, monkeypatch, reports)
    env._app_path = str(bundle)
    env._app_launched_at = 1000.0

    assert env.app_crash_artifacts() == []


def test_the_executable_name_comes_from_the_bundles_info_plist(tmp_path: Path) -> None:
    # `ios.bundle_id` is `com.example.Showcase`, and an `.ips` filename names the *process*
    # (`Showcase-2026-…ips`), never the bundle id — so the sweep's pattern cannot be built from the
    # one name the environment already held.
    bundle = _app_bundle(tmp_path, executable="Showcase")
    assert _bundle_executable(str(bundle)) == "Showcase"
    assert _bundle_executable(str(tmp_path / "missing.app")) is None


def test_read_report_answers_empty_bytes_for_a_report_that_vanished(tmp_path: Path) -> None:
    # A report store is a live directory: a file listed a moment ago can be gone by the time it is
    # read, which is neither evidence about the app nor a reason to fail the sweep.
    assert _read_report(tmp_path / "gone.ips") == b""


def test_the_udid_check_reads_the_payload_not_the_filename(tmp_path: Path) -> None:
    payload = f'{{"header":1}}\n{{"procPath":"/Devices/{_UDID}/data/app"}}\n'.encode()
    assert _reports_device(payload, _UDID) is True
    assert _reports_device(payload, _OTHER_UDID) is False


# --- iOS: the launch marker ------------------------------------------------------------------------


def _record(label: str, calls: list[str]) -> None:
    calls.append(label)


def test_a_relaunch_step_re_stamps_the_launch_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    # `device_relauncher`'s closure has no environment in scope to update, so without this override
    # the marker would stay at the lease's cold launch and a crash after a mid-scenario `relaunch`
    # would sweep back far enough to attach an `.ips` that `relaunch` itself already superseded.
    env = xcuitest_environment.XcuitestEnvironment("xcuitest", _UDID)
    env._app_launched_at = 1000.0
    calls: list[str] = []
    # Snapshot the marker from *inside* the inner call, not after it returns: asserting only that
    # the marker moved by the time the wrapper itself returns would also pass a wrapper that stamps
    # *after* calling `inner` — this is what actually pins the "before" half of "stamped before the
    # call" (BE-0424).
    seen_from_inside: list[float | None] = []

    def fake_relauncher(
        self: object,
        eff: object,
        scenario: object,
        driver: object,
        *,
        extra_env: object = None,
    ) -> Callable[[object], None]:
        def inner(step: object) -> None:
            seen_from_inside.append(env._app_launched_at)
            _record("inner", calls)

        return inner

    monkeypatch.setattr(ios_environment._DeviceEnvironment, "relauncher", fake_relauncher)
    from bajutsu.common.scenario import Relaunch, Scenario

    relaunch = env.relauncher(
        _ios_eff(None),
        Scenario.model_validate({"name": "s", "steps": [{"tap": {"id": "x"}}]}),
        FakeDriver([]),
    )
    relaunch(Relaunch())

    assert calls == ["inner"]
    assert seen_from_inside == [env._app_launched_at]
    assert seen_from_inside[0] is not None and seen_from_inside[0] > 1000.0


def test_a_crawl_reset_re_stamps_the_launch_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    # A crawl records several crashes per run and keeps walking, so an unstamped marker would make
    # the second crash's sweep reach back past its own reset and attach the *first* crash's report.
    env = xcuitest_environment.XcuitestEnvironment("xcuitest", _UDID)
    env._app_launched_at = 1000.0
    calls: list[str] = []
    seen_from_inside: list[float | None] = []

    def fake_crawl_reset(self: object, eff: object) -> Callable[[object], None]:
        def inner(driver: object) -> None:
            seen_from_inside.append(env._app_launched_at)
            _record("inner", calls)

        return inner

    monkeypatch.setattr(ios_environment._DeviceEnvironment, "crawl_reset", fake_crawl_reset)
    env.crawl_reset(_ios_eff(None))(FakeDriver([]))

    assert calls == ["inner"]
    assert seen_from_inside == [env._app_launched_at]
    assert seen_from_inside[0] is not None and seen_from_inside[0] > 1000.0


def test_a_cold_spawn_stamps_the_launch_marker_before_spawning_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The app is launched *inside* the runner, by its own `XCUIApplication.launch()`, so there is no
    # Python-side launch call to stamp beside — this pins that the marker is set before
    # `_spawn_cold_with_retry` is even called, not merely that it ends up set by the time `start`
    # returns (which would also pass a stamp taken *after* the spawn).
    env = xcuitest_environment.XcuitestEnvironment("xcuitest", _UDID)
    env._prepare_simulator = lambda *a, **kw: None  # type: ignore[method-assign]
    env._launch_params = lambda *a, **kw: ({}, [])  # type: ignore[method-assign]
    monkeypatch.setattr(
        xcuitest_environment, "_resolve_runner", lambda *a, **kw: Path("/tmp/Runner.xctestrun")
    )

    seen: list[float | None] = []

    def fake_spawn_with_retry(spawn: object, **kw: object) -> object:
        seen.append(env._app_launched_at)
        return SimpleNamespace(
            driver=FakeDriver([]),
            ready=lambda: True,
            poll=lambda: None,
            log_tail=lambda: "",
            discard=lambda: None,
        )

    monkeypatch.setattr(xcuitest_environment, "_spawn_cold_with_retry", fake_spawn_with_retry)

    env._spawn_cold(_ios_eff(None), Preconditions(), "simulator", None, None)

    assert seen == [env._app_launched_at]
    assert seen[0] is not None


def test_resume_warm_stamps_the_launch_marker_before_launching_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cross-lease warm-reuse launch (BE-0291) — the item's own "worst case" for a stale marker:
    # left unstamped, a scenario resuming warm on this device would sweep back far enough to match an
    # *earlier* scenario's `.ips`, which passes both the executable-name and the udid check, being the
    # same app on the same Simulator.
    env = xcuitest_environment.XcuitestEnvironment("xcuitest", _UDID)
    env._app_launched_at = 1000.0
    env._prepare_simulator = lambda *a, **kw: None  # type: ignore[method-assign]
    env._launch_params = lambda *a, **kw: ({}, [])  # type: ignore[method-assign]
    monkeypatch.setattr("bajutsu.common.backend_cli.simctl.Env.terminate", lambda self, b: None)

    from bajutsu.common.backend_cli import simctl

    seen: list[float | None] = []

    def fake_launch(
        self: simctl.Env, bundle_id: str, args: object = (), env_vars: object = None
    ) -> None:
        seen.append(env._app_launched_at)

    monkeypatch.setattr(simctl.Env, "launch", fake_launch)

    env._resume_warm(_ios_eff(None), Preconditions(), None, None, FakeDriver([]))

    assert seen == [env._app_launched_at]
    assert seen[0] is not None and seen[0] > 1000.0


# --- Android: the launch marker and the two capture layers -----------------------------------------


def _android(run: Callable[[list[str]], str]) -> AndroidEnvironment:
    return AndroidEnvironment("adb", "emulator-5554", adb_run=run)


def _marker_run(calls: list[list[str]], *, marker: str) -> Callable[[list[str]], str]:
    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        if any("date " in part for part in cmd):
            return marker
        return ""

    return run


def test_start_stamps_the_launch_marker_before_launching_the_app() -> None:
    # `e.launch` is `am start -W`, which waits for the launch to complete — a marker stamped after it
    # returns is already later than the launch it names, and would reject the `.ips`/`logcat` evidence
    # of an app that crashed during that very launch (Unit 13's own "startup-crash" case: the one
    # shape neither showcase fixture, which taps its trigger mid-scenario, can exercise on real
    # hardware).
    from test_adb_lifecycle import _eff, _resolve_activity_run

    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run(calls))
    env.start(_eff(), Preconditions())

    joined = [" ".join(c) for c in calls]
    date_index = next(
        i for i, c in enumerate(joined) if c.startswith("adb -s emulator-5554 shell date")
    )
    launch_index = next(i for i, c in enumerate(joined) if "am start" in c)
    assert date_index < launch_index


def test_relauncher_stamps_the_launch_marker_before_launching_the_app() -> None:
    from test_adb_lifecycle import _eff, _resolve_activity_run

    from bajutsu.common.scenario import Relaunch, Scenario

    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run(calls))
    scenario = Scenario.model_validate(
        {"name": "s", "preconditions": {}, "steps": [{"tap": {"id": "x"}}]}
    )
    # Two elements satisfy `await_ready`'s bare-count rung so the closure returns without spinning.
    relaunch = env.relauncher(_eff(), scenario, FakeDriver([_el("a", "A"), _el("b", "B")]))
    relaunch(Relaunch())

    joined = [" ".join(c) for c in calls]
    date_index = next(
        i for i, c in enumerate(joined) if c.startswith("adb -s emulator-5554 shell date")
    )
    launch_index = next(i for i, c in enumerate(joined) if "am start" in c)
    assert date_index < launch_index


def test_crawl_reset_stamps_the_launch_marker_before_launching_the_app() -> None:
    from test_adb_lifecycle import _eff, _resolve_activity_run

    calls: list[list[str]] = []
    env = AndroidEnvironment("adb", "emulator-5554", adb_run=_resolve_activity_run(calls))
    env.crawl_reset(_eff())(FakeDriver([_el("a", "A"), _el("b", "B")]))

    joined = [" ".join(c) for c in calls]
    date_index = next(
        i for i, c in enumerate(joined) if c.startswith("adb -s emulator-5554 shell date")
    )
    launch_index = next(i for i, c in enumerate(joined) if "am start" in c)
    assert date_index < launch_index


def test_the_launch_marker_is_one_read_split_into_three_renderings() -> None:
    # Three separate `date` invocations would stamp three different instants and let the `logcat`
    # field (read last) filter out a crash that landed in the gap between reads.
    calls: list[list[str]] = []
    env = _android(_marker_run(calls, marker="1700000000|2026-09-16 10:00:00|09-16 10:00:00.000\n"))
    env._stamp_launch_marker()

    date_reads = [c for c in calls if any("date " in part for part in c)]
    assert len(date_reads) == 1
    assert env._launch_marker == (1700000000.0, "2026-09-16 10:00:00")
    assert env._logcat_marker == "09-16 10:00:00.000"


def test_a_malformed_marker_read_leaves_every_consumer_unable_to_confirm() -> None:
    env = _android(lambda _c: "nonsense\n")
    env._stamp_launch_marker()
    assert env._launch_marker is None
    assert env._logcat_marker is None


def test_a_non_numeric_epoch_field_leaves_every_consumer_unable_to_confirm() -> None:
    # The right field count but a corrupt epoch — the marker must not half-succeed.
    env = _android(lambda _c: "not-a-number|2026-09-16 10:00:00|09-16 10:00:00.000\n")
    env._stamp_launch_marker()
    assert env._launch_marker is None
    assert env._logcat_marker is None


def test_a_failing_marker_read_resolves_to_no_marker_rather_than_raising() -> None:
    def run(cmd: list[str]) -> str:
        raise subprocess.CalledProcessError(1, cmd)

    env = _android(run)
    env._stamp_launch_marker()
    assert env._launch_marker is None
    assert env._logcat_marker is None


def test_the_logcat_layer_extracts_only_this_packages_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ours = (
        "FATAL EXCEPTION: main\n"
        "Process: com.example.app, PID: 4242\n"
        "java.lang.IllegalStateException: boom\n"
    )
    env = _android(lambda cmd: ours if "logcat" in cmd else "")
    env._package = "com.example.app"
    env._logcat_marker = "09-16 10:00:00.000"

    found = env.app_crash_artifacts()
    assert [name for name, _ in found] == ["logcat-crash.txt"]
    assert b"IllegalStateException" in found[0][1]


def test_the_logcat_layer_rejects_another_packages_block_in_the_same_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The buffer is device-global across processes as well as across launches, so the time bound
    # alone is not enough: a system service faulting in the same window must never be attached.
    monkeypatch.setattr(android_environment, "_LOGCAT_TIMEOUT", 0.05)
    monkeypatch.setattr(android_environment, "_LOGCAT_POLL", 0.001)
    theirs = "FATAL EXCEPTION: main\nProcess: com.other.app, PID: 9\njava.lang.Error: theirs\n"
    env = _android(lambda cmd: theirs if "logcat" in cmd else "")
    env._package = "com.example.app"
    env._logcat_marker = "09-16 10:00:00.000"

    assert env.app_crash_artifacts() == []


def test_the_logcat_layer_polls_rather_than_trusting_one_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `crash_dump` writes a native `>>> <process> <<<` block only *after* the death the detection
    # already keyed off, so a single `-d` snapshot can legitimately come up empty.
    monkeypatch.setattr(android_environment, "_LOGCAT_TIMEOUT", 1.0)
    monkeypatch.setattr(android_environment, "_LOGCAT_POLL", 0.001)
    dumps = {"n": 0}

    def run(cmd: list[str]) -> str:
        if "logcat" not in cmd:
            return ""
        dumps["n"] += 1
        if dumps["n"] < 3:
            return "09-16 10:00:01.000 I/Nothing: fine\n"
        return ">>> com.example.app <<<\nsignal 11 (SIGSEGV)\nbacktrace:\n  #00 pc 0\n"

    env = _android(run)
    env._package = "com.example.app"
    env._logcat_marker = "09-16 10:00:00.000"

    assert env.app_crash_artifacts() != []
    assert dumps["n"] >= 3


def test_a_failing_logcat_dump_resolves_to_nothing_rather_than_raising() -> None:
    def run(cmd: list[str]) -> str:
        if "logcat" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return ""

    env = _android(run)
    env._package = "com.example.app"
    env._logcat_marker = "09-16 10:00:00.000"

    assert env.app_crash_artifacts() == []


def test_an_unexpected_exception_in_the_logcat_layer_resolves_to_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The outer `try`/`except Exception` around the whole body: not only an `adb` failure, since none
    # of them can change the app's own crash verdict.
    env = _android(lambda _c: "")
    env._package = "com.example.app"
    env._logcat_marker = "09-16 10:00:00.000"

    def explode() -> list[tuple[str, bytes]]:
        raise ValueError("boom")

    monkeypatch.setattr(env, "_logcat_crash", explode)
    assert env.app_crash_artifacts() == []


def test_the_logcat_layer_needs_both_a_package_and_a_marker() -> None:
    env = _android(lambda _c: "FATAL EXCEPTION: main\nProcess: com.example.app, PID: 1\n")
    env._package = None
    env._logcat_marker = "09-16 10:00:00.000"
    assert env.app_crash_artifacts() == []

    env._package = "com.example.app"
    env._logcat_marker = None
    assert env.app_crash_artifacts() == []


def test_the_tombstone_pull_restores_the_devices_privilege_level() -> None:
    # `adb root` persists device-wide until `adb unroot` or a reboot, and nothing else in this
    # repository restores it. Leaked, a later scenario's `install` / `pm clear` / `force_stop` would
    # run through a root shell — and `AdbDriver._rooted()` caches `id -u`, so a two-finger gesture
    # that should fail loudly with `UnsupportedAction` would instead run and pass, decided by whether
    # an earlier, unrelated scenario happened to crash.
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        if "stat" in " ".join(cmd):
            return "1700000005 /data/tombstones/tombstone_00\n"
        if cmd[-2:] == ["id", "-u"]:
            return "2000\n"
        if "cat" in cmd:
            return "backtrace:\n  #00 pc 0000\n"
        return ""

    env = _android(run)
    env._launch_marker = (1700000000.0, "2026-09-16 10:00:00")

    found = env.app_crash_tombstone()
    flat = [" ".join(c) for c in calls]
    assert [name for name, _ in found] == ["tombstone_00"]
    assert any("adb -s emulator-5554 root" in c for c in flat)
    assert any("adb -s emulator-5554 unroot" in c for c in flat)


def test_the_unroot_runs_even_when_the_pull_itself_failed() -> None:
    # The restore is in a `finally`: a pull that fell over must not leave the device rooted for every
    # later scenario on it.
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        if "stat" in " ".join(cmd):
            raise subprocess.CalledProcessError(1, cmd)
        if cmd[-2:] == ["id", "-u"]:
            return "2000\n"
        return ""

    env = _android(run)
    env._launch_marker = (1700000000.0, "2026-09-16 10:00:00")

    assert env.app_crash_tombstone() == []
    assert any("unroot" in " ".join(c) for c in calls)


def test_an_unroot_that_fails_to_run_is_logged_rather_than_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Distinct from a *refused* unroot (still root afterward): here the `adb unroot` command itself
    # never completes, which must not raise past the best-effort wrapper either.
    def run(cmd: list[str]) -> str:
        if "stat" in " ".join(cmd):
            return ""
        if "unroot" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return ""

    env = _android(run)
    env._launch_marker = (1700000000.0, "2026-09-16 10:00:00")

    with caplog.at_level("WARNING"):
        assert env.app_crash_tombstone() == []

    assert any("could not restore adbd" in r.message for r in caplog.records)


def test_a_refused_unroot_is_logged_loudly_rather_than_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Pinning only that `unroot` is *called* would miss a restore that was attempted but never took
    # effect — the case that silently changes how every later scenario on the device actuates. The
    # device must answer "not root" on `_pull_tombstone`'s own pre-check (else `_restore_unroot`
    # would skip the restore entirely as an already-rooted device, per the test below) and "still
    # root" only afterward, once the refused `unroot` has had its chance to run.
    id_u_calls = 0

    def run(cmd: list[str]) -> str:
        nonlocal id_u_calls
        if "stat" in " ".join(cmd):
            return ""
        if cmd[-2:] == ["id", "-u"]:
            id_u_calls += 1
            return "2000\n" if id_u_calls == 1 else "0\n"
        return ""

    env = _android(run)
    env._launch_marker = (1700000000.0, "2026-09-16 10:00:00")

    with caplog.at_level("WARNING"):
        env.app_crash_tombstone()

    assert any("still running as root" in r.message for r in caplog.records)


def test_restore_unroot_leaves_an_already_rooted_device_alone() -> None:
    # An outer harness that rooted the device for a whole session (`demos/showcase/android/Makefile`'s
    # `e2e` / `e2e-conformance` targets both do, for scenarios like `gestures` that require it) must
    # keep that root after this call — an unconditional `adb unroot` would drop it mid-session, decided
    # by whether an earlier, unrelated scenario happened to crash.
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        if "stat" in " ".join(cmd):
            return "1700000005 /data/tombstones/tombstone_00\n"
        if cmd[-2:] == ["id", "-u"]:
            return "0\n"  # already root before this call
        if "cat" in cmd:
            return "backtrace:\n  #00 pc 0000\n"
        return ""

    env = _android(run)
    env._launch_marker = (1700000000.0, "2026-09-16 10:00:00")

    found = env.app_crash_tombstone()
    flat = [" ".join(c) for c in calls]
    assert [name for name, _ in found] == ["tombstone_00"]
    assert any("adb -s emulator-5554 root" in c for c in flat)
    assert not any("unroot" in c for c in flat)


def test_the_tombstone_bound_compares_device_epochs_on_both_sides() -> None:
    # Never a device-side rendering resolved through the *host's* timezone: a UTC emulator driven
    # from any other zone would otherwise place every fresh tombstone hours from the marker.
    listing = "1699999999 /data/tombstones/tombstone_00\n1700000005 /data/tombstones/tombstone_01\n"
    assert android_environment._newest_tombstone(listing, 1700000000.0) == "tombstone_01"
    assert android_environment._newest_tombstone(listing, 1700000010.0) is None


def test_the_tombstone_bound_skips_the_protobuf_sibling() -> None:
    # On API 30+ `debuggerd` writes both `tombstone_NN` (text) and `tombstone_NN.pb` (protobuf), and
    # the `tombstone_*` glob the shell command reads matches both. A newer `.pb` sibling must not win
    # over the text file — `cat`-ing binary protobuf through the redacting text path would mangle or
    # fail to read it, silently dropping the whole layer.
    listing = (
        "1700000005 /data/tombstones/tombstone_00\n1700000010 /data/tombstones/tombstone_00.pb\n"
    )
    assert android_environment._newest_tombstone(listing, 1700000000.0) == "tombstone_00"


def test_the_tombstone_layer_needs_a_launch_marker() -> None:
    calls: list[list[str]] = []
    env = _android(_marker_run(calls, marker=""))
    env._launch_marker = None

    assert env.app_crash_tombstone() == []
    assert calls == []  # never rooted the device to find that out
