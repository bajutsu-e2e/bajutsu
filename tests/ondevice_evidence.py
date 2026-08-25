"""Per-test video + deviceLog capture for the on-device adb/XCUITest pytest suites (BE-0350).

`conformance (adb)`, `fault-injection (adb)`, and their iOS twins drive their backend straight from
pytest (`launch_driver`, never `bajutsu run`), so none of them inherits the scenario pipeline's
evidence capture (`bajutsu/evidence/core.py`'s `capture:`-driven `FileSink`) — a failure in any of
them has no video or device log to diagnose it, unlike every scenario-driven CI job. This module
wires the same interval primitives the pipeline itself uses (`bajutsu.evidence.intervals`) directly
around each test, the way `demos/showcase/android/screenrecord.py` already does for the codegen
lane's `connectedAndroidTest` — no bajutsu runtime there either, so no scenario/YAML `capture:`
machinery to hook into. `capture()` itself is backend-agnostic: the caller supplies `start_video`/
`start_log`, e.g. `android_screenrecord`/`intervals.start_logcat` for adb or `xcuitest_video`/
`start_device_log` for XCUITest — `android_screenrecord` below pre-binds the adb-specific video
bounds both Android suites share.

Recorded per test, not per module: `screenrecord`'s ~180s device-side ceiling (see
`intervals.SCREENRECORD_TIME_LIMIT_S`'s comment) would truncate a single video spanning the whole
conformance module, and a per-test clip also lets a failure's video be found by its own test name
rather than scrubbing one long recording. Kept only on failure — the same policy `screenrecord.py`'s
own Makefile target already applies to the codegen lane — so the fixture itself contributes nothing
to `runs/` on a green run, and CI artifact storage tracks failures, not suite size.

The keep/discard decision cannot live inside `capture`'s own fixture teardown: pytest builds the
"teardown" `TestReport` only *after* every finalizer for the item has run, `capture`'s own included,
so a fixture cannot see whether an *earlier-torn-down sibling fixture's* teardown half also failed.
The decision is deferred to the `pytest_runtest_makereport` hook below instead, which fires again
once that "teardown" report exists — the one point that has seen the whole attempt.

A module opts in with one autouse fixture:

    @pytest.fixture(autouse=True)
    def _evidence(request: pytest.FixtureRequest) -> Iterator[None]:
        yield from capture(
            SERIAL, "conformance-adb", request,
            start_video=android_screenrecord, start_log=intervals.start_logcat,
        )
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import shutil
import subprocess
import warnings
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import pytest

from bajutsu import adb
from bajutsu.evidence import intervals

_logger = logging.getLogger(__name__)

# This attempt's outcome so far, reset at "setup" and OR-accumulated afterward — reset, not just
# latched, because `backend_crash_recovery` (BE-0334) re-runs a whole item via `_initrequest()` on an
# infra-fault retry, reusing the same `pytest.Item` (and stash): a flag that only ever turned True
# would still read failed on a later, recovered, passing attempt.
_FAILED: pytest.StashKey[bool] = pytest.StashKey()

# Evidence directories `capture()` registered this attempt, swept by the hook below once the
# "teardown" report says the attempt is clean — never inside `capture()` itself (see the module
# docstring). Registered unconditionally, regardless of whether either `start_*` call below
# succeeded: `_FAILED` alone decides keep vs. discard, so a directory left unregistered here would
# never be swept even on a passing test — the opposite of "kept only on failure".
_PENDING: pytest.StashKey[list[Path]] = pytest.StashKey()


def android_screenrecord(serial: str, path: Path) -> intervals.Interval:
    """`intervals.start_screenrecord` pre-bound to `intervals`' shared size/bit-rate/time-limit bounds
    (the same ones `demos/showcase/android/screenrecord.py` pre-binds for the codegen lane).

    `confirm_started=True`: a bare spawn only proves the local `adb shell` client started, not that a
    device-side `screenrecord` process exists yet, and a fast failing case can otherwise tear down
    before it does — the exact case this module's evidence exists to explain, shipping an absent or
    unplayable mp4 for it. It confirms less than the iOS signal does, though
    (`_await_screenrecord_started`: a live process is not proof frames are being emitted), and it
    warns rather than raises on timeout — `capture()`'s missing/empty check stays the backstop.

    `start_screenrecord` always records to the one fixed device-side path `adb.VIDEO_DEVICE_PATH`,
    and its post-stop transform pulls whatever is there unconditionally. Both Android suites reuse
    that path once per test, so a prior test's `stop()` swallowing a transient pull failure
    (`capture()`'s own backstop, on an otherwise-passing test) can leave its clip behind on the
    device; if the *next* test's own recording then fails to start — `confirm_started` only warns,
    it does not skip the pull — that stale clip gets pulled in as the next test's evidence: non-empty,
    so `capture()`'s missing/empty check never notices. Clearing the device-side file before every
    spawn closes both causes; a failure to remove it is suppressed rather than raised, since
    refusing to record would cost this test's own evidence too — but it does leave that window
    open, because the path is overwritten only if the spawn that follows actually starts, which is
    exactly what the case above does not do.
    """
    with contextlib.suppress(subprocess.CalledProcessError, OSError):
        adb._real_run(adb.rm_cmd(serial, adb.VIDEO_DEVICE_PATH))
    return intervals.start_screenrecord(
        serial,
        path,
        time_limit=intervals.SCREENRECORD_TIME_LIMIT_S,
        size=intervals.SCREENRECORD_SIZE,
        bit_rate=intervals.SCREENRECORD_BIT_RATE,
        confirm_started=True,
    )


def xcuitest_video(udid: str, path: Path) -> intervals.Interval:
    """`intervals.start_video` with `confirm_started=True` — the XCUITest twin of the start
    confirmation `android_screenrecord` pre-binds; see its docstring for why a bare spawn isn't
    enough. Only that: iOS has no counterpart to that helper's size/bit-rate/time-limit bounds, so a
    `recordVideo` clip here is bounded only by how long its own test runs."""
    return intervals.start_video(udid, path, confirm_started=True)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """Track this attempt's outcome, and sweep its pending evidence once teardown is the last word.

    Deferred to the "teardown" report specifically: by the time it exists, every finalizer for this
    attempt — `capture`'s own and any sibling fixture's — has already run, so this is the first point
    that has actually seen the whole attempt, not just its "call" phase.
    """
    report = yield
    if report.when == "setup":
        item.stash[_FAILED] = report.failed
    else:
        item.stash[_FAILED] = item.stash.get(_FAILED, False) or report.failed
    if report.when == "teardown":
        pending = item.stash.get(_PENDING, [])
        item.stash[_PENDING] = []
        if not item.stash.get(_FAILED, False):
            for dest in pending:
                shutil.rmtree(dest, ignore_errors=True)
    return report


def _slug(nodeid: str) -> str:
    """A filesystem-safe, collision-resistant directory name for a test's evidence.

    Collapsing every disallowed character run to a single `_` is not by itself injective — a
    parametrize id differing only in, say, a space vs. a slash can collapse onto the same readable
    prefix — and a collision would let one test's kept evidence silently overwrite another's. The
    trailing hash of the *full* nodeid keeps distinct nodeids apart regardless.
    """
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)
    digest = hashlib.sha1(nodeid.encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{readable}_{digest}"


class _CaptureNode(Protocol):
    """The slice of a pytest item `capture` reads: the node's id, and its stash for the pending set."""

    @property
    def nodeid(self) -> str: ...

    @property
    def stash(self) -> pytest.Stash: ...


class _CaptureRequest(Protocol):
    """The slice of `pytest.FixtureRequest` `capture` reads: the test's node.

    Named rather than taking the whole fixture request so a test can drive this with a node-only
    stub instead of manufacturing a real request (BE-0388).
    """

    @property
    def node(self) -> _CaptureNode: ...


def capture(
    serial: str,
    lane: str,
    request: _CaptureRequest,
    *,
    start_video: Callable[[str, Path], intervals.Interval],
    start_log: Callable[[str, Path], intervals.Interval],
) -> Iterator[None]:
    """Record video + deviceLog for `request`'s test; keep the files under `runs/` only if it failed.

    `lane` names the CI job (e.g. "conformance-adb") so its own uploaded artifact is self-contained.
    `start_video`/`start_log` take only `(serial_or_udid, path)` — this function is backend-agnostic,
    so every caller states explicitly which backend's primitives it wants (`android_screenrecord` +
    `intervals.start_logcat` for adb, `xcuitest_video` + `start_device_log` for XCUITest) —
    and a test can pass a fake pair to exercise this without a real device (BE-0350).
    """
    dest = Path("runs") / lane / _slug(request.node.nodeid)
    # Start every attempt from an empty directory: simctl `recordVideo` (no `--force`) refuses to
    # overwrite an existing file, so a clip kept by a crashed attempt — or by an earlier local run of
    # the same test — would otherwise silently stand in for this attempt's own evidence. `adb pull`
    # and `start_logcat`'s own `wb` open would have overwritten either file regardless, but clearing
    # first makes that true for both backends without relying on it. Guarded like the two starters
    # below: preparing the directory is capture plumbing too, so it must not decide a gating
    # driver-contract verdict either.
    try:
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
    except OSError:
        _logger.warning(
            "%s: could not prepare the evidence directory; the test runs without capture",
            dest,
            exc_info=True,
        )
        yield
        return
    video: intervals.Interval | None = None
    log: intervals.Interval | None = None
    try:
        video = start_video(serial, dest / "video.mp4")
    except Exception:
        # Diagnostic evidence is best-effort: a transient `xcrun`/`adb` hiccup *starting* a capture
        # (a fork failure, the binary transiently missing) must not decide a gating driver-contract
        # verdict — both `conformance` suites sit inside the required `E2E` aggregates. Whatever did
        # start is still stopped in the `finally` below.
        _logger.warning(
            "%s: could not start the video; the test runs without it", dest, exc_info=True
        )
    try:
        log = start_log(serial, dest / "device.log")
    except Exception:
        # Caught separately from the video above: the two fail for unrelated reasons (an `OSError`
        # opening `device.log` says nothing about `recordVideo`), and sharing one `try` would drop
        # the cheaper, more diagnostic artifact whenever the video's spawn happened to fail first.
        _logger.warning(
            "%s: could not start the device log; the test runs without it", dest, exc_info=True
        )
    finally:
        # Registered regardless of whether either starter succeeded: a start failure is warned
        # about above, not raised, so it never reaches `_FAILED` — leaving `dest` unregistered here
        # would keep whatever `start_video` alone managed to record (a real `adb pull`ed video.mp4,
        # on adb) on a test that then *passes*. `_FAILED` is the only thing that decides keep vs.
        # discard now; see `_PENDING`'s own comment.
        request.node.stash.setdefault(_PENDING, []).append(dest)
    try:
        yield
    finally:
        # Stop whichever of the two actually started, so a test that crashed — or a `start_log` that
        # raised right after a real `start_video` spawned a device-side `screenrecord` — never leaves
        # a recording running past its own test. Nested so a raising `video.stop()` (e.g. a failed
        # `adb pull`, which `start_screenrecord` deliberately lets propagate) still leaves `log.stop()`
        # called rather than orphaning the logcat process.
        #
        # Whether that propagates past this fixture depends on what the attempt has shown so far: if
        # setup or the test's own call already failed, this is already going to redden the lane, so
        # let it surface loudly, same as any other teardown failure. If the test passed, though, a
        # transient `adb pull` / finalize hiccup is the capture tooling glitching, not the driver
        # contract under test — so failing the case over it would only turn a passing test red for
        # evidence that is usually discarded anyway. Warn instead. "Usually", not always: `_FAILED`
        # cannot yet see a sibling fixture torn down *before* this one failing its own teardown
        # (that report comes later), so on that path the hook keeps a directory whose video this
        # warning has just said may be missing, and the log line is the only record of why.
        already_failing = request.node.stash.get(_FAILED, False)
        try:
            try:
                if video is not None:
                    video.stop()
            finally:
                if log is not None:
                    log.stop()
        except Exception:
            if already_failing:
                raise
            _logger.warning(
                "%s's capture failed to stop cleanly on an otherwise-passing test; discarding it "
                "anyway",
                dest,
                exc_info=True,
            )
        finally:
            # In a `finally`, not plain trailing code: the `except` above re-raises when the attempt
            # was already failing, and that is exactly the path whose directory the hook is about to
            # *keep* — a human downloading it deserves the same missing/empty signal as every discard
            # path gets. The only remaining signal that a capture actually recorded: `_spawn` discards
            # the child's stderr, so a `recordVideo`/`screenrecord` that refused to start or died
            # leaves no other trace, and `if-no-files-found: ignore` on the CI upload step would let
            # the silence pass for a clean run.
            for artifact in (dest / "video.mp4", dest / "device.log"):
                if not artifact.exists() or artifact.stat().st_size == 0:
                    warnings.warn(
                        f"{artifact} is missing or empty: this test recorded no evidence",
                        stacklevel=2,
                    )
