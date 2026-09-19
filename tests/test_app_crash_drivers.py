"""The two backends' `app_crash_signal()` implementations (BE-0424).

Both answer the same question — "has the app under test crashed *right now*" — from very different
evidence, and both have to fail closed rather than guess. These tests pin the up-front scopes each
declares (a real iOS device, an Android device below API 30, an `AdbDriver` built with no package),
because every one of them exists to stop the probe confirming something it cannot actually see.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import AdbDriver, adb_driver
from bajutsu.common.drivers.xcuitest import XcuitestDriver
from bajutsu.common.drivers.xcuitest._reply import _Reply

_LAUNCHED = (1_700_000_000.0, "2026-09-16 10:00:00")


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the exit-info poll's real-time bound for the fast suite.

    The bound is a condition wait, so shortening it changes only how long an *unconfirmed* answer
    takes to give up — never whether a matching entry is found. Left at its production value these
    tests would spend seconds of wall clock each proving nothing extra.
    """
    monkeypatch.setattr(adb_driver, "_EXIT_INFO_TIMEOUT", 0.05)
    monkeypatch.setattr(adb_driver, "_EXIT_INFO_POLL", 0.001)


# --- iOS ------------------------------------------------------------------------------------------


def _ios(state: str, *, real_device: bool = False) -> tuple[XcuitestDriver, list[str]]:
    """An `XcuitestDriver` whose `/app/state` answers `state`, plus the paths it requested."""
    paths: list[str] = []

    def transport(method: str, path: str, body: object) -> _Reply:
        paths.append(path)
        return _Reply(status="ok", app_state=state)

    driver = XcuitestDriver(transport=transport, sleep=lambda _s: None, is_real_device=real_device)
    return driver, paths


def test_ios_confirms_a_crash_only_on_not_running() -> None:
    driver, _ = _ios("notRunning")
    assert driver.app_crash_signal() is not None


def test_ios_does_not_confirm_a_backgrounded_app() -> None:
    # The reason `app.state` is used instead of the element tree: a deliberate `background` step, or
    # a system alert covering the app, leaves an empty or unexpected tree but never `notRunning`.
    for state in ("runningForeground", "runningBackground", "runningBackgroundSuspended"):
        driver, _ = _ios(state)
        assert driver.app_crash_signal() is None, state


def test_ios_does_not_confirm_on_an_unknown_state() -> None:
    # XCTest's own "cannot tell" must read as no confirmation, not as evidence either way.
    driver, _ = _ios("unknown")
    assert driver.app_crash_signal() is None


def test_ios_answers_none_on_a_real_device_without_asking() -> None:
    # Scoped to the Simulator: a real device jetsam-kills a foreground app under memory pressure, so
    # `notRunning` there is not the proof it is on the Simulator — and the device's own crash reports
    # never reach this host to be attached anyway. Checked *before* the route, so no round trip.
    driver, paths = _ios("notRunning", real_device=True)
    assert driver.app_crash_signal() is None
    assert paths == []


def test_ios_lets_a_channel_error_propagate() -> None:
    # Deliberately not swallowed into `None`: a dead channel is the existing
    # `XcuitestRunnerCrashError`, a `BackendCrashError`, and this item leaves it to the recovery path
    # that already owns it. A route failing right after this step's own selector failure is ordinary
    # contention, not evidence the channel is unrelated to this step.
    from bajutsu.common.drivers.xcuitest import XcuitestRunnerCrashError

    def transport(method: str, path: str, body: object) -> _Reply:
        raise XcuitestRunnerCrashError("the runner is gone")

    driver = XcuitestDriver(transport=transport, sleep=lambda _s: None)
    try:
        driver.app_crash_signal()
    except base.BackendCrashError:
        return
    raise AssertionError("a channel error must propagate, not resolve to None")


def test_the_xcuitest_driver_declares_the_capability() -> None:
    driver, _ = _ios("runningForeground")
    assert isinstance(driver, base.AppCrashSignal)


# --- Android ---------------------------------------------------------------------------------------


def _adb(
    run: Callable[[list[str]], str],
    *,
    package: str | None = "com.example.app",
    api_level: int | None = 34,
    launched_at: tuple[float, str] | None = _LAUNCHED,
) -> AdbDriver:
    return AdbDriver(
        "SERIAL",
        run=run,
        package=package,
        api_level=api_level,
        launched_at=(lambda: launched_at) if launched_at is not None else None,
    )


def _no_process(exit_info: str) -> Callable[[list[str]], str]:
    """An `adb` whose `pidof` reports no process and whose exit-info answers `exit_info`."""

    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="")
        if "exit-info" in cmd:
            return exit_info
        return ""

    return run


def _exit_info_entry(
    reason: str, timestamp: str, *, n: int = 0, reason_punctuation: str = ""
) -> str:
    """One `ApplicationExitInfo` entry, shaped like real `dumpsys` output (BE-0424).

    `AppExitInfoTracker.dumpLocked` (frameworks/base) frames each entry as an `ApplicationExitInfo`
    block and renders `timestamp=` *before* `reason=` — the field order the parser must handle, not
    the reversed one a hand-written fixture could get away with matching. `reason_punctuation` models
    the numeric-code decoration AOSP surrounds the reason token with across revisions (`"4 "`,
    `" (4)"`, `"(4)"`) — no live device was available to pin the exact shape, so the parser matches
    the reason as a substring rather than betting on one punctuation guess, and these fixtures
    exercise more than one shape to hold it to that.
    """
    return (
        f"  {n}: ApplicationExitInfo{{\n"
        f"    timestamp={timestamp}\n"
        f"    pid=4242\n"
        f"    process=com.example.app\n"
        f"    reason={reason_punctuation}{reason}\n"
        f"  }}\n"
    )


def test_android_confirms_a_crash_recorded_after_this_launch() -> None:
    driver = _adb(_no_process(_exit_info_entry("CRASH", "2026-09-16 10:00:05")))
    signal = driver.app_crash_signal()
    assert signal is not None
    assert "CRASH" in signal


def test_android_confirms_a_native_crash_too() -> None:
    driver = _adb(_no_process(_exit_info_entry("CRASH_NATIVE", "2026-09-16 10:00:05")))
    assert driver.app_crash_signal() is not None


def test_android_does_not_confirm_an_ordinary_exit() -> None:
    # An empty `pidof` is necessary but not sufficient: it matches an ordinary process exit just as
    # well as a crash, which is exactly what the exit-info corroboration is for.
    for reason in ("USER_REQUESTED", "ANR", "LOW_MEMORY"):
        driver = _adb(_no_process(_exit_info_entry(reason, "2026-09-16 10:00:05")))
        assert driver.app_crash_signal() is None, reason


def test_android_does_not_confirm_a_crash_from_before_this_launch() -> None:
    # The history persists across process lifetimes, so its newest entry alone is not reliable: an
    # earlier scenario's crash on the same package can still be the newest one reported.
    driver = _adb(_no_process(_exit_info_entry("CRASH", "2026-09-16 09:59:00")))
    assert driver.app_crash_signal() is None


def test_android_does_not_confirm_while_the_app_still_holds_a_process() -> None:
    def run(cmd: list[str]) -> str:
        return "4242\n" if "pidof" in cmd else ""

    assert _adb(run).app_crash_signal() is None


def test_android_fails_closed_with_no_package_and_never_touches_adb() -> None:
    # `dumpsys activity exit-info` with no package reports *every* package on the device, so a
    # silently defaulted `None` would let this confirm another process's crash as this app's.
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return ""

    assert _adb(run, package=None).app_crash_signal() is None
    assert calls == []


def test_android_fails_closed_below_api_30_and_never_touches_adb() -> None:
    # `ApplicationExitInfo` does not exist below API 30, so polling for it would time out on every
    # real crash instead of failing closed immediately.
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return ""

    assert _adb(run, api_level=29).app_crash_signal() is None
    assert _adb(run, api_level=None).app_crash_signal() is None
    assert calls == []


def test_android_fails_closed_with_no_launch_marker() -> None:
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return ""

    assert _adb(run, launched_at=None).app_crash_signal() is None
    assert calls == []


def test_android_does_not_confirm_when_pidof_returns_empty_with_no_exception() -> None:
    # The `if ... .strip():` guard's false branch, reached when `pidof` answers empty stdout
    # directly rather than through the `CalledProcessError` toybox actually raises — defensive, but
    # real for any `RunFn` that does not model that quirk.
    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            return ""
        return _exit_info_entry("CRASH", "2026-09-16 10:00:05")

    assert _adb(run).app_crash_signal() is not None


def test_android_treats_an_exit_info_failure_as_cannot_confirm() -> None:
    # A `pidof` that confirms no process, followed by an `adb` transport failure on the
    # corroborating exit-info read — distinct from `pidof` itself failing.
    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="")
        raise OSError("adb is not on PATH")

    assert _adb(run).app_crash_signal() is None


def test_android_treats_an_adb_failure_as_cannot_confirm() -> None:
    # An unhandled fault on the first real crash this probe is meant to catch would be strictly worse
    # than no answer, so a transport hiccup resolves to `None` like every other gap.
    def run(cmd: list[str]) -> str:
        raise OSError("adb is not on PATH")

    assert _adb(run).app_crash_signal() is None


def test_android_reads_a_pidof_exit_with_output_as_not_a_match() -> None:
    # toybox `pidof` exits 1 on no match — the routine, expected outcome — but any *other* non-zero
    # exit is a genuine failure and must not be read as "the process is gone".
    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(2, cmd, output="")
        raise AssertionError("exit-info must not be reached on a genuine pidof failure")

    assert _adb(run).app_crash_signal() is None


def test_android_polls_rather_than_trusting_a_single_exit_info_read() -> None:
    # `pidof` reports empty the instant the process dies, while `system_server` records the matching
    # `ApplicationExitInfo` only after it reaps the death — so a single read races the very crash it
    # is meant to corroborate.
    reads = {"n": 0}

    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="")
        reads["n"] += 1
        if reads["n"] < 3:
            return _exit_info_entry("USER_REQUESTED", "2026-09-16 09:00:00")
        return _exit_info_entry("CRASH", "2026-09-16 10:00:05")

    assert _adb(run).app_crash_signal() is not None
    assert reads["n"] >= 3


def test_the_exhausted_latch_stops_a_later_probe_re_paying_the_bound() -> None:
    # The first probe is the one racing the reap; a later one in the same scenario has nothing new to
    # wait out. After one bounded poll comes up empty, every later probe reads the history once.
    reads = {"n": 0}

    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="")
        reads["n"] += 1
        return "reason=USER_REQUESTED\n  timestamp=2026-09-16 09:00:00\n"

    driver = _adb(run)
    assert driver.app_crash_signal() is None
    first = reads["n"]
    assert first > 1  # the first probe really did poll

    assert driver.app_crash_signal() is None
    assert reads["n"] == first + 1  # the second read the history exactly once


def test_reset_exit_info_poll_restores_the_bound_for_a_fresh_launch() -> None:
    # A genuinely fresh launch — not "the screen moved" — is what makes waiting on a new reap worth
    # paying for again, which is why this is its own method rather than folded into
    # `invalidate_settled_cache()`, whose contract every ordinary gesture already triggers.
    reads = {"n": 0}

    def run(cmd: list[str]) -> str:
        if "pidof" in cmd:
            raise subprocess.CalledProcessError(1, cmd, output="")
        reads["n"] += 1
        return _exit_info_entry("USER_REQUESTED", "2026-09-16 09:00:00")

    driver = _adb(run)
    driver.app_crash_signal()
    bounded = reads["n"]
    driver.app_crash_signal()  # latched: one read
    driver.reset_exit_info_poll()
    driver.app_crash_signal()

    assert reads["n"] > bounded + 1  # the third probe polled again


def test_the_adb_driver_declares_the_capability() -> None:
    assert isinstance(_adb(lambda _c: ""), base.AppCrashSignal)
    assert isinstance(_adb(lambda _c: ""), base.AppCrashPollResettable)


def test_the_reset_reaches_the_driver_through_a_tracing_wrapper() -> None:
    # `_reset_exit_info_poll` checks `isinstance(driver, base.AppCrashPollResettable)`, not a
    # concrete `AdbDriver` check — the same shape `SettledCacheInvalidator` is checked with, so the
    # reset keeps working when `--trace-driver` wraps the driver `TracingDriver` installs the
    # protocol's members as real instance attributes precisely so this reads true through it.
    from bajutsu.common.drivers.tracing import TracingDriver

    driver = _adb(lambda _c: "")
    wrapped: base.Driver = TracingDriver(driver)
    assert isinstance(wrapped, base.AppCrashPollResettable)


# --- the parsers the two signals share -------------------------------------------------------------


def test_newest_exit_info_reads_the_newest_entry_only() -> None:
    # `dumpsys` prints the history newest first, and only the newest matters: an older entry is an
    # earlier lifetime's, which the caller's time bound exists to rule out.
    text = _exit_info_entry("CRASH", "2026-09-16 10:00:05", n=1) + _exit_info_entry(
        "USER_REQUESTED", "2026-09-15 08:00:00", n=0
    )
    assert adb.newest_exit_info(text) == ("CRASH", "2026-09-16 10:00:05")


def test_newest_exit_info_never_pairs_one_entrys_reason_with_anothers_timestamp() -> None:
    # A single-pass scan that accepts the first `timestamp=` once any `reason=` has been seen would
    # pair the newest entry's crash with an *older* entry's timestamp once real (multi-entry, blank
    # framing) output is fed through it — this is the regression the block split exists to prevent.
    text = _exit_info_entry("CRASH", "2026-09-16 10:00:05", n=1) + _exit_info_entry(
        "USER_REQUESTED", "2026-09-01 08:00:00", n=0
    )
    reason, timestamp = adb.newest_exit_info(text)  # type: ignore[misc]
    assert (reason, timestamp) == ("CRASH", "2026-09-16 10:00:05")
    assert timestamp != "2026-09-01 08:00:00"


def test_newest_exit_info_answers_none_on_an_empty_history() -> None:
    assert adb.newest_exit_info("") is None
    assert adb.newest_exit_info("no exit info recorded\n") is None


def test_newest_exit_info_tolerates_a_numeric_code_around_the_reason_token() -> None:
    # The exact punctuation `reasonCodeToString`'s numeric constant is decorated with varies across
    # AOSP revisions and could not be pinned against a live device, so the parser matches the reason
    # as a substring rather than a fixed shape. Each variant below must still classify as `CRASH`.
    for punctuation in ("", "4 ", "4: ", "(4) "):
        text = _exit_info_entry("CRASH", "2026-09-16 10:00:05", reason_punctuation=punctuation)
        assert adb.newest_exit_info(text) == ("CRASH", "2026-09-16 10:00:05"), punctuation


def test_newest_exit_info_skips_a_block_whose_reason_names_no_known_token() -> None:
    # An entry whose `reason=` value matches none of the tokens this item classifies (`OTHER`,
    # `FREEZER`, `PACKAGE_UPDATED`, …) must not stop the sweep — it has to fall through to an older
    # entry rather than answering `None` outright while a real crash sits right behind it.
    text = _exit_info_entry("OTHER", "2026-09-16 10:00:05", n=1) + _exit_info_entry(
        "CRASH", "2026-09-16 09:00:00", n=0
    )
    assert adb.newest_exit_info(text) == ("CRASH", "2026-09-16 09:00:00")


def test_newest_exit_info_does_not_confuse_crash_native_with_crash() -> None:
    # `CRASH_NATIVE` contains `CRASH` as a substring, so the native token has to be checked first —
    # otherwise every native crash would misclassify as a plain managed one.
    text = _exit_info_entry("CRASH_NATIVE", "2026-09-16 10:00:05")
    assert adb.newest_exit_info(text) == ("CRASH_NATIVE", "2026-09-16 10:00:05")


def test_a_managed_crash_block_is_bounded_to_the_target_process() -> None:
    # The crash buffer is device-global across processes as well as across launches, so a system
    # service faulting in the same window must never be written as this scenario's evidence.
    ours = (
        "FATAL EXCEPTION: main\n"
        "Process: com.example.app, PID: 4242\n"
        "java.lang.IllegalStateException: boom\n"
    )
    theirs = (
        "FATAL EXCEPTION: main\n"
        "Process: com.other.app, PID: 99\n"
        "java.lang.IllegalStateException: not ours\n"
    )
    assert adb.extract_crash_block(ours, "com.example.app") is not None
    assert adb.extract_crash_block(theirs, "com.example.app") is None


def test_a_native_crash_block_is_bounded_the_same_way() -> None:
    native = ">>> com.example.app <<<\nsignal 11 (SIGSEGV)\nbacktrace:\n  #00 pc 0000\n"
    assert adb.extract_crash_block(native, "com.example.app") is not None
    assert adb.extract_crash_block(native, "com.other.app") is None


def test_no_crash_block_at_all_answers_none() -> None:
    assert (
        adb.extract_crash_block("09-16 10:00:00.000 I/Nothing: fine\n", "com.example.app") is None
    )


def test_a_foreign_block_does_not_absorb_a_second_block_that_follows_it_closely() -> None:
    # A block bounded only by the line cap, never by the next header, would let a system service's
    # crash — landing within the same window as the app's own — swallow the app's block into the
    # foreign one's match window and return the wrong process's trace.
    theirs_then_ours = (
        "FATAL EXCEPTION: main\n"
        "Process: com.other.app, PID: 99\n"
        "java.lang.IllegalStateException: not ours\n"
        "FATAL EXCEPTION: main\n"
        "Process: com.example.app, PID: 4242\n"
        "java.lang.IllegalStateException: ours\n"
    )
    block = adb.extract_crash_block(theirs_then_ours, "com.example.app")
    assert block is not None
    assert "com.other.app" not in block
    assert "com.example.app" in block
