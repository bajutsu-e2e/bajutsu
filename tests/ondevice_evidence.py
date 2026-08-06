"""Per-test video + deviceLog capture for the on-device adb/XCUITest pytest suites.

`conformance (adb)`, `fault-injection (adb)`, and their iOS twins drive their backend straight from
pytest (`launch_driver`, never `bajutsu run`), so none of them inherits the scenario pipeline's
evidence capture (`bajutsu/evidence/core.py`'s `capture:`-driven `FileSink`) — a failure in any of
them has no video or device log to diagnose it, unlike every scenario-driven CI job. This module
wires the same interval primitives the pipeline itself uses (`bajutsu.evidence.intervals`) directly
around each test, the way `demos/showcase/android/screenrecord.py` already does for the codegen
lane's `connectedAndroidTest` — no bajutsu runtime there either, so no scenario/YAML `capture:`
machinery to hook into. `capture()` itself is backend-agnostic: the caller supplies `start_video`/
`start_log`, e.g. `intervals.start_screenrecord`/`start_logcat` for adb or `intervals.start_video`/
`start_device_log` for XCUITest — `android_screenrecord` below pre-binds the adb video bound both
Android suites share.

Recorded per test, not per module: `screenrecord`'s ~180s device-side ceiling (see
`screenrecord.py`) would truncate a single video spanning the whole conformance module, and a
per-test clip also lets a failure's video be found by its own test name rather than scrubbing one
long recording. Kept only on failure — the same policy `screenrecord.py`'s own Makefile target
already applies to the codegen lane — so a green run uploads nothing and CI artifact storage tracks
failures, not suite size.

A module opts in with one autouse fixture:

    @pytest.fixture(autouse=True)
    def _evidence(request: pytest.FixtureRequest) -> Iterator[None]:
        yield from capture(
            SERIAL, "conformance-adb", request,
            start_video=android_screenrecord, start_log=intervals.start_logcat,
        )
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from bajutsu.evidence import intervals

# Set on the item's stash by the makereport hook below to the *current* report's outcome — the only
# way a fixture finalizer can learn pytest's own outcome, since a `TestReport` is not otherwise
# visible from teardown. Always overwritten, never just latched: `backend_crash_recovery` (BE-0334)
# re-runs a whole item via `_initrequest()` on an infra-fault retry, reusing the same `pytest.Item`,
# so a stash that only ever turned True would still read True on a later attempt that recovered and
# passed — keeping a crashed attempt's evidence after it stopped mattering, and worse, doing so after
# that same attempt's `capture()` call already overwrote the video/log files a passing attempt no
# longer needs kept.
_FAILED: pytest.StashKey[bool] = pytest.StashKey()

# Mirrors screenrecord.py's bound: small enough for `/sdcard` and the artifact upload, well under
# the platform's ~180s ceiling for any single conformance/fault-injection case.
_TIME_LIMIT_S = 180
_SIZE = "540x1200"
_BIT_RATE = 2_000_000


def android_screenrecord(serial: str, path: Path) -> intervals.Interval:
    """`intervals.start_screenrecord` pre-bound to this module's size/bit-rate/time-limit bounds."""
    return intervals.start_screenrecord(
        serial, path, time_limit=_TIME_LIMIT_S, size=_SIZE, bit_rate=_BIT_RATE
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Tag the item's stash with the current report's outcome, for `capture`'s finalizer to read."""
    report = yield
    item.stash[_FAILED] = report.failed
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


def capture(
    serial: str,
    lane: str,
    request: pytest.FixtureRequest,
    *,
    start_video: Callable[[str, Path], intervals.Interval],
    start_log: Callable[[str, Path], intervals.Interval],
) -> Iterator[None]:
    """Record video + deviceLog for `request`'s test; keep the files under `runs/` only if it failed.

    `lane` names the CI job (e.g. "conformance-adb") so its own uploaded artifact is self-contained.
    `start_video`/`start_log` take only `(serial_or_udid, path)` — this function is backend-agnostic,
    so every caller states explicitly which backend's primitives it wants (`android_screenrecord` +
    `intervals.start_logcat` for adb, `intervals.start_video` + `start_device_log` for XCUITest) —
    and a test can pass a fake pair to exercise this without a real device.
    """
    dest = Path("runs") / lane / _slug(request.node.nodeid)
    dest.mkdir(parents=True, exist_ok=True)
    video: intervals.Interval | None = None
    log: intervals.Interval | None = None
    # Default to keeping the evidence. The only way to *prove* it may be discarded is to reach past
    # `yield` and read a "call"-phase report that already says the test passed — reachable only when
    # this function's own setup (the two `start_*` calls above) also succeeded. If either raises
    # before `yield`, the `finally` below runs as part of that very unwind, strictly before pytest's
    # own "setup"-phase report is even produced — so the stash could never carry `_FAILED` in time,
    # and defaulting to discard would delete the one piece of evidence that setup failure needs most.
    keep = True
    try:
        video = start_video(serial, dest / "video.mp4")
        log = start_log(serial, dest / "device.log")
        yield
        keep = request.node.stash.get(_FAILED, False)
    finally:
        # Stop whichever of the two actually started, before the keep/discard decision, so a test that
        # crashed — or a `start_log` that raised right after a real `start_video` spawned a device-side
        # `screenrecord` — never leaves a recording running past its own test. Nested twice: the inner
        # pair lets a raising `video.stop()` (e.g. a failed `adb pull`, which `start_screenrecord`
        # deliberately lets propagate) still leave `log.stop()` called rather than orphaning the logcat
        # process; the outer pair lets the keep/discard decision run even when a `stop()` call itself
        # raised, so a transient stop failure on an otherwise-passing test still discards its evidence
        # rather than leaving it behind by accident.
        try:
            try:
                if video is not None:
                    video.stop()
            finally:
                if log is not None:
                    log.stop()
        finally:
            if not keep:
                shutil.rmtree(dest, ignore_errors=True)
