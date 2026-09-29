"""crawl's own app-crash capture: gated on driver confirmation, `logcat`-only, best-effort (BE-0424).

`crawl`'s *detection* stays the UI-tree heuristic it always used — `test_crawl_records_a_crash_when_
app_ui_collapses` already pins that. What this item adds is the *capture* that rides alongside it,
and the capture has a shape detection does not: it must cost nothing on the common case (a covering
alert, not a real crash) and it must never take down the whole walk on a transport hiccup.
"""

from __future__ import annotations

import subprocess

from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.crawl import crawl as crawl_engine
from bajutsu.crawl.core._functions import _confirmed_app_crash_artifacts

_REPORT = ("logcat-crash.txt", b"FATAL EXCEPTION: main\n")


class _CrashSignalDriver(FakeDriver):
    """A `FakeDriver` that also confirms an app crash through `base.AppCrashSignal`."""

    def __init__(self, *a: object, signal: str | None = "the app is gone", **kw: object) -> None:
        super().__init__(*a, **kw)  # type: ignore[arg-type]
        self.signal = signal

    def app_crash_signal(self) -> str | None:
        return self.signal


def test_a_confirmed_crash_captures_the_lanes_evidence() -> None:
    driver = _CrashSignalDriver([])
    assert _confirmed_app_crash_artifacts(driver, lambda: [_REPORT]) == (_REPORT,)


def test_an_unconfirmed_crash_captures_nothing() -> None:
    # The gate a UI-tree false positive must not pay a full-timeout sweep on: `crawl`'s own detection
    # can trip on a covering system alert, which this driver reports as "cannot confirm".
    driver = _CrashSignalDriver([], signal=None)
    calls: list[int] = []

    def sweep() -> list[tuple[str, bytes]]:
        calls.append(1)
        return [_REPORT]

    assert _confirmed_app_crash_artifacts(driver, sweep) == ()
    assert calls == []  # the sweep itself never ran


def test_a_driver_without_the_capability_captures_nothing() -> None:
    driver = FakeDriver([])
    assert _confirmed_app_crash_artifacts(driver, lambda: [_REPORT]) == ()


def test_a_backend_crash_error_from_the_probe_never_escapes() -> None:
    # `crawl` owns no recovery path for an escaping `BackendCrashError`: unswallowed, it would
    # propagate through `_run`'s failure handling, skip `_finish`, and discard every repro and
    # artifact buffered for the entire walk — on exactly the failure this exists to capture for.
    class _Explodes(FakeDriver):
        def app_crash_signal(self) -> str | None:
            raise base.BackendCrashError("channel dead")

    assert _confirmed_app_crash_artifacts(_Explodes([]), lambda: [_REPORT]) == ()


def test_an_os_or_subprocess_error_from_the_probe_never_escapes() -> None:
    # The wider catch this call site needs alongside the driver's own fix: nothing under
    # `backend_cli/adb/` raises `BackendCrashError`, so a bare catch would still miss a `pidof` /
    # exit-info failure that reached here unswallowed.
    class _Explodes(FakeDriver):
        def app_crash_signal(self) -> str | None:
            raise subprocess.CalledProcessError(1, ["adb"])

    assert _confirmed_app_crash_artifacts(_Explodes([]), lambda: [_REPORT]) == ()


def test_a_plain_channel_error_from_the_probe_never_escapes() -> None:
    # `XcuitestChannelError` (a channel reply the driver cannot decode) is a bare `RuntimeError`, not
    # a `BackendCrashError` — only its `XcuitestRunnerCrashError` subclass is also one. A catch naming
    # only `BackendCrashError` would let an ordinary iOS decode hiccup through unswallowed.
    class _Explodes(FakeDriver):
        def app_crash_signal(self) -> str | None:
            raise RuntimeError("runner replied with non-JSON")

    assert _confirmed_app_crash_artifacts(_Explodes([]), lambda: [_REPORT]) == ()


def test_crawl_attaches_artifacts_to_a_confirmed_crash() -> None:
    home = [el(identifier="home.boom", traits=["button"])]
    crashed = [el(traits=["application"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.boom":
            d.screen = list(crashed)

    driver = _CrashSignalDriver(list(home), react=react)

    def reset(d: base.Driver) -> None:
        assert isinstance(d, FakeDriver)
        d.screen = list(home)

    screen_map = crawl_engine(
        driver, reset, max_screens=50, max_steps=100, app_crash_artifacts=lambda: [_REPORT]
    )
    assert len(screen_map.crashes) == 1
    assert screen_map.crashes[0].artifacts == (_REPORT,)


def test_crawl_records_no_artifacts_when_the_driver_cannot_confirm() -> None:
    # The detection is unchanged — a `Crash` is still recorded on the UI-tree heuristic alone — only
    # the artifact sweep is gated, so an unconfirmed crash records a `Crash` with no `artifacts`.
    home = [el(identifier="home.boom", traits=["button"])]
    crashed = [el(traits=["application"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.boom":
            d.screen = list(crashed)

    driver = _CrashSignalDriver(list(home), react=react, signal=None)

    def reset(d: base.Driver) -> None:
        assert isinstance(d, FakeDriver)
        d.screen = list(home)

    screen_map = crawl_engine(
        driver, reset, max_screens=50, max_steps=100, app_crash_artifacts=lambda: [_REPORT]
    )
    assert len(screen_map.crashes) == 1
    assert screen_map.crashes[0].artifacts == ()


def test_crawl_with_no_capture_wired_records_no_artifacts() -> None:
    # A caller that never threads `app_crash_artifacts` (a lane whose environment has none) must not
    # crash on the default: the neutral capture answers `[]` and the walk continues.
    home = [el(identifier="home.boom", traits=["button"])]
    crashed = [el(traits=["application"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and isinstance(arg, dict) and arg.get("id") == "home.boom":
            d.screen = list(crashed)

    driver = _CrashSignalDriver(list(home), react=react)

    def reset(d: base.Driver) -> None:
        assert isinstance(d, FakeDriver)
        d.screen = list(home)

    screen_map = crawl_engine(driver, reset, max_screens=50, max_steps=100)
    assert screen_map.crashes[0].artifacts == ()
