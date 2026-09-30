"""Start, stop, and adopt an interval capture, correcting its origin against its own spawn."""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from bajutsu.common import stall_diagnostics
from bajutsu.common.backend_cli import adb, simctl
from bajutsu.common.evidence import media

from ._subprocess_proc import _SubprocessProc
from .interval import Interval
from .proc import Proc

_logger = logging.getLogger(__name__)
ADB_PROVIDER = "adb"
_VIDEO_FINALIZE_TIMEOUT = 120.0
# How long `confirm_started` waits for a recording's start signal — simctl's `Recording started` on
# stderr, or the Android device-side process appearing — before giving up. Generous versus reported
# simctl/adb startup jitter, small versus the finalize timeouts above; the report's video-sync
# correction just goes uncorrected for this scenario on timeout. Overridable per lane, like the
# CI-sensitive xcuitest timeouts it sits beside (BE-0348).
_VIDEO_START_TIMEOUT = 5.0
_VIDEO_START_TIMEOUT_ENV = "BAJUTSU_VIDEO_START_TIMEOUT"
# The line `simctl io recordVideo` writes to stderr once its first frame has been processed. Its own
# `--help` names this as the signal to wait on, and it is the *only* one it offers: the mp4 stays
# zero bytes for the whole recording and is written whole at finalize, so a file-growth probe — what
# stood here before — could never confirm a start and spent its entire ceiling on every scenario.
_RECORDING_STARTED = "Recording started"
# How far *before* its own spawn a measured origin may sit before `_measured_start` stops trusting
# it. Covers the ordinary sub-frame slop — a container rounds its duration up to a whole frame, and
# the spawn instant is stamped just before a recorder that begins a beat later — without admitting a
# nominal-frame-rate timeline, whose excess grows with the recording rather than staying inside one
# frame.
_ORIGIN_SLACK = 0.2
# The far side of that same window: how long after its spawn a recorder may plausibly have opened
# its first frame. This is a claim about the *recorder* — measured at 0.15s for `simctl io
# recordVideo` on Xcode 26.6 — not about how patient a lane chose to be, so it is deliberately not
# `_video_start_timeout()`. Tying the two let `BAJUTSU_VIDEO_START_TIMEOUT: "20"` silently widen the
# iOS lane's origin acceptance to twenty seconds, admitting origins no recording can actually have.
_ORIGIN_STARTUP_CEILING = 5.0
# The shared tail of both confirmation-timeout warnings (iOS start signal, Android device-side
# process), so the two backends always describe the same condition identically. It claims only what
# the timeout establishes — that the *confirmation* is unavailable. The anchor can still be measured
# from the finished recording's own duration, which is not known until `stop()`, so an operator
# debugging a seek must not read this as "the anchor is wrong".
_ANCHOR_UNCORRECTED_MSG = (
    "this scenario's recording start could not be confirmed; its report seek offsets fall back to "
    "the scenario's own start unless the finished recording can state its duration"
)


# spawn(argv, stdout_path) -> a running process (stdout written to the file if given)
Spawn = Callable[[list[str], "Path | None"], Proc]


# How often the growth check asks the device for the recording's size. Deliberately coarser than
# either sibling poll, because this one is the only poll of the three that is both *device-side* and
# *purely diagnostic*. The iOS twin (`_STDERR_POLL`) also polls at 0.05s, but it `pread`s the
# child's captured stderr on the host — a read that costs nothing.
# `_await_screenrecord_started` also round-trips to the device, but at 0.2s because, when the
# first-bytes wait (`_await_screenrecord_first_bytes`, which waits device-side in one shell rather
# than polling from here) could not answer, its answer *is* the video anchor, so its resolution is
# the measurement. This check answers only yes or no, and it
# sits on the critical path: `AndroidEnvironment` prestarts the recording immediately
# before it launches the app, so every probe here is an `adb shell` round trip and a device-side
# shell spawn competing with a cold start on a two-core emulator. One second resolves "is it
# producing?" just as well as a fifth of one, at a fifth of the traffic.
_SCREENRECORD_GROWTH_POLL = 1.0
# How often `_await_screenrecord_started` asks the device for a new `screenrecord` pid. Also the
# smallest budget that wait is handed, so it always makes at least one probe even when the
# first-bytes wait before it spent their shared deadline.
_SCREENRECORD_PID_POLL = 0.2


# --- appTrace: pair start/finish log markers into timed intervals ---
#
# os_signpost intervals are meant for Instruments and do not show up in `log stream`,
# so appTrace works off ordinary log markers: a message "<name> started" opens an
# interval and "<name> finished" (or ended/done) closes it, timed by the log stamps.

_BEGIN = re.compile(r"^(?P<name>.+?) (?:started|begin)$")
_END = re.compile(r"^(?P<name>.+?) (?:finished|ended|done|end)$")


def record_video_cmd(udid: str, path: str) -> list[str]:
    """Build the simctl command that records the screen to `path` (h264)."""
    # Validate the udid inline — as simctl's own builders and adb's `screenrecord_cmd`
    # (via `checked_serial`) do — so this evidence-capture argv can't carry an option-injecting
    # / metacharacter id even if reached without the earlier `simctl.Env` boundary check.
    return [
        "xcrun",
        "simctl",
        "io",
        simctl.validated_udid(udid),
        "recordVideo",
        "--codec",
        "h264",
        path,
    ]


def device_log_cmd(udid: str, predicate: str | None = None) -> list[str]:
    """Build the simctl command that streams the device log, optionally filtered by `predicate`."""
    cmd = [
        "xcrun",
        "simctl",
        "spawn",
        simctl.validated_udid(udid),
        "log",
        "stream",
        "--level",
        "debug",
        "--style",
        "compact",
    ]
    if predicate:
        cmd += ["--predicate", predicate]
    return cmd


def app_trace_cmd(udid: str, subsystem: str) -> list[str]:
    """Build the simctl command that streams the app's os_log `subsystem` as ndjson."""
    return [
        "xcrun",
        "simctl",
        "spawn",
        simctl.validated_udid(udid),
        "log",
        "stream",
        "--predicate",
        f'subsystem == "{subsystem}"',
        "--style",
        "ndjson",
    ]


def _video_start_timeout() -> float:
    """The recording-start confirmation ceiling in seconds, from the env override or the default.

    Rejects a non-finite override (`inf`, `-inf`, `nan`) rather than passing it through `float()`'s
    successful parse: `inf` would turn the bounded confirmation poll unbounded — the fixed-wait
    determinism guarantee this whole timeout family exists to keep (prime directive 2) — and `nan`
    would silently produce a 0-second timeout (`max(0.0, nan)` keeps `0.0`, since every comparison
    against `nan` is `False`) rather than falling back to the compiled default like any other
    malformed value.
    """
    raw = os.environ.get(_VIDEO_START_TIMEOUT_ENV)
    if not raw:
        return _VIDEO_START_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return _VIDEO_START_TIMEOUT
    return max(0.0, value) if math.isfinite(value) else _VIDEO_START_TIMEOUT


def spawn(argv: list[str], stdout_path: Path | None) -> Proc:
    """The real `Spawn`: run `argv` as a child, sending its stdout to `stdout_path`.

    Every capture below takes this as an injectable default, so a test can swap in a fake
    child without a device.
    """
    return _SubprocessProc(argv, stdout_path)


def _measured_start(path: Path, ended_at: float, spawned_at: float | None) -> float | None:
    """Where `path`'s footage begins on the monotonic clock, or None when it cannot be measured.

    A recording states its own duration, and `ended_at` is when it stopped, so the subtraction gives
    the instant its first frame was captured — the origin a report's seek offsets are relative to
    (the origin BE-0346 approximates with `true_start`).

    The subtraction is only as good as its two inputs, and each has a way of being wrong that the
    other cannot see:

    - The duration may not be a *wall-clock* measure, which is a property of the recorder rather
      than of this arithmetic: a container written at a nominal frame rate can state more seconds
      than the recorder was ever open for. That pushes the origin *before* the spawn.
    - `ended_at` may not be when the recording ended, because a recorder can stop itself. Android's
      `screenrecord` does exactly that at its own `SCREENRECORD_TIME_LIMIT_S` ceiling, so a scenario
      outlasting that ceiling stops signalling a recorder that quit minutes ago. That pushes the
      origin *after* the first frame, by the whole gap — silently worse than the proxy it outranks.

    `spawned_at` bounds both, because it is the one instant that needs no confirmation. A recording
    opens on its first frame somewhere between that spawn and `_ORIGIN_STARTUP_CEILING`; an origin
    outside that window says one of the two inputs is not describing this recording, so it is
    discarded rather than trusted. Debug rather than warning on every rejection: falling back to the
    start-confirmation proxy is the behavior every run had before this, not an evidence gap this
    function created.
    """
    duration = media.duration_seconds(path)
    if duration is None:
        _logger.debug("could not read %s's duration; its anchor stays on the start proxy", path)
        return None
    if spawned_at is None:
        # No bound, no measurement: a provider that stamps no spawn leaves nothing to check the two
        # inputs against, and an unchecked origin is exactly what the window below exists to refuse.
        _logger.debug("%s has no spawn instant to bound its origin; it stays on the proxy", path)
        return None
    origin = ended_at - duration
    # `_ORIGIN_SLACK` on the near side only: a sub-frame overshoot is ordinary rounding, while the
    # far side is the recorder's real startup and has a ceiling of its own.
    if not (spawned_at - _ORIGIN_SLACK) <= origin <= (spawned_at + _ORIGIN_STARTUP_CEILING):
        _logger.debug(
            "%s states %.3fs of footage ending at the stop, which puts its first frame %+.3fs from "
            "the spawn — outside the window a recording can open in, so its anchor stays on the "
            "start proxy",
            path,
            duration,
            origin - spawned_at,
        )
        return None
    return origin


def adopt(interval: Interval, target: Path) -> Interval:
    """Wrap an already-running interval so `stop()` finalizes it, then relocates its file to `target`.

    Android starts its video *before* the app launches, so the cold-start frames are
    captured; that recording writes to a temporary path. The sink adopts the running capture at
    scenario start and, on stop, moves the finalized file to the scenario's artifact path — the real
    finalize (the wrapped interval's stop signal and timeout) still runs, this only redirects the
    result. The web lane finalizes in place instead; this is the device twin of that adopt-on-stop
    shape. Carries `interval`'s `true_start` and `start_confirmed` forward unchanged: the wrapped
    interval already settled when — and whether — it actually began, and neither answer moves just
    because its file is later relocated — nor does `spawned_at`, the span bound `stop()` checks the
    relocated file's duration against, or `measure_origin`, which the wrapped interval's own start
    settled and which decides whether that check runs at all. `measured_start` is deliberately *not* carried: it is
    settled by a `stop()`, and this wrapper's own stop is the one that sees the relocated file.
    """

    def relocate(_: Path) -> Path:
        finalized = interval.stop()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(finalized), str(target))
        return target

    return Interval(
        kind=interval.kind,
        path=target,
        provider=interval.provider,
        true_start=interval.true_start,
        start_confirmed=interval.start_confirmed,
        spawned_at=interval.spawned_at,
        measure_origin=interval.measure_origin,
        _transform=relocate,
    )


def start_video(
    udid: str, path: Path, spawn: Spawn = spawn, *, confirm_started: bool = False
) -> Interval:
    """Begin recording the screen to `path`; stop() (SIGINT) finalizes the mp4.

    The stop gives `recordVideo` the generous `_VIDEO_FINALIZE_TIMEOUT` to write and mux the clip: a
    premature kill would truncate the mp4 (no `moov` atom) and wedge the simulator's recording session.
    `confirm_started`, when set, waits for simctl's own `Recording started` line on stderr, so the
    returned `Interval.true_start` reflects when the first frame was processed rather than when the
    process was spawned. A confirmation that gives up also triggers the BE-0361 stall capture, which
    is a bounded, opt-in side effect: a dead video pipeline is evidence about the device, not just
    about this clip.
    """
    spawned_at = time.monotonic()
    proc = spawn(record_video_cmd(udid, str(path)), None)
    # Resolved per call, not bound as a parameter default: a default binds at import time and so
    # could never see `BAJUTSU_VIDEO_START_TIMEOUT` (BE-0348).
    true_start = (
        proc.await_stderr(_RECORDING_STARTED, _video_start_timeout()) if confirm_started else None
    )
    start_confirmed = (true_start is not None) if confirm_started else None
    if start_confirmed is False:
        _logger.warning(
            "recordVideo did not report %r for %s within %ss; %s",
            _RECORDING_STARTED,
            path,
            _video_start_timeout(),
            _ANCHOR_UNCORRECTED_MSG,
        )
        # A recording that never reports its first frame says the Simulator's video pipeline is
        # degraded *now*, which is the same stall the runner channel dies of — so capture the state
        # while it is still there (BE-0361 unit 2). Keyed on the tri-state the `Interval` already
        # models rather than re-deriving it, and fired here rather than inside the wait because the
        # udid the capture screenshots is a parameter of this function. Opt-in and bounded; unset,
        # it does nothing.
        stall_diagnostics.capture("video-no-bytes", stall_diagnostics.simulator_probes(udid))
    return Interval(
        kind="video",
        path=path,
        true_start=true_start,
        start_confirmed=start_confirmed,
        spawned_at=spawned_at,
        _proc=proc,
        _stop_signal=signal.SIGINT,
        _stop_timeout=_VIDEO_FINALIZE_TIMEOUT,
    )


def start_device_log(
    udid: str, path: Path, predicate: str | None = None, spawn: Spawn = spawn
) -> Interval:
    """Begin streaming the device log to `path`; stop() (SIGTERM) ends the stream."""
    proc = spawn(device_log_cmd(udid, predicate), path)
    return Interval(kind="deviceLog", path=path, _proc=proc, _stop_signal=signal.SIGTERM)


# --- adb (Android) interval providers: the twins of the simctl starters above ---


def _await_screenrecord_stopped(
    serial: str, run: adb.RunFn, timeout: float = _VIDEO_FINALIZE_TIMEOUT, poll: float = 0.2
) -> None:
    """Wait until the device-side `screenrecord` has exited, before its mp4 is pulled.

    On the stop signal the local `adb shell` client returns as soon as the connection closes, but the
    device-side `screenrecord` is still writing the mp4's `moov` atom. Pulling then races that write
    and yields a truncated, moov-less (unplayable) file — the Android recording instability. Poll to a
    bounded deadline (a condition wait on the process's exit, not a fixed sleep). If the probe can't
    run or never clears, proceed anyway so it can never hang the run — the pull stays best-effort —
    but log a warning: the pull may then copy a still-finalizing (truncated, moov-less) mp4, the very
    failure this wait exists to prevent, so it must not be silent.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not run(adb.screenrecord_pids_cmd(serial)).strip():
                return
        except (subprocess.CalledProcessError, OSError) as exc:
            _logger.warning(
                "could not probe device-side screenrecord (%s); pulling anyway — the video may be "
                "truncated (no moov atom)",
                exc,
            )
            return
        time.sleep(poll)
    _logger.warning(
        "device-side screenrecord still running after %ss; pulling anyway — the video may be "
        "truncated (no moov atom)",
        timeout,
    )


def _screenrecord_pids(serial: str, run: adb.RunFn) -> set[str]:
    """The device-side `screenrecord` pids right now, or an empty set on a probe failure."""
    try:
        return {pid for pid in run(adb.screenrecord_pids_cmd(serial)).split() if pid}
    except (subprocess.CalledProcessError, OSError) as exc:
        # An empty baseline reads as "nothing was running", so a failed probe silently disables the
        # leaked-process guard the baseline exists for — disclose it rather than mistime silently.
        _logger.warning(
            "could not probe device-side screenrecord on %s before spawning (%s); a leaked "
            "recording from an earlier attempt may now confirm a start that never happened",
            serial,
            exc,
        )
        return set()


def _clear_screenrecord_target(serial: str, run: adb.RunFn, device_path: str) -> bool:
    """Remove a leftover recording at `device_path` before a new one spawns; whether that worked.

    `_await_screenrecord_first_bytes` reads "the file has a byte" as "the recorder muxed its first
    frame", which is only true of a file this spawn created: an earlier attempt's finalized mp4
    already has bytes, and `screenrecord` truncates it only once it opens the path. A failed removal
    is disclosed and hands the anchor back to the pid confirmation, which a stale file cannot fool.
    """
    try:
        run(adb.rm_cmd(serial, device_path))
    except (subprocess.CalledProcessError, OSError) as exc:
        _logger.warning(
            "could not clear %s on %s before spawning screenrecord (%s); its start is confirmed by "
            "the device-side process instead of its first bytes",
            device_path,
            serial,
            exc,
        )
        return False
    return True


def _await_screenrecord_first_bytes(
    serial: str, run: adb.RunFn, device_path: str, timeout: float
) -> float | None:
    """The instant the device-side recording first held a byte, or None when it never did in time.

    `screenrecord` opens its output empty and writes nothing until the muxer starts, which happens
    when the encoder hands over its first frame — so the file's first byte is the closest signal to
    the recording's origin the device offers, closer than the process merely existing (the encoder
    is not even configured then). The wait runs device-side in one `adb shell`
    (`adb.await_file_bytes_cmd`), and the instant is stamped on its return: late by at most one
    device poll plus the round trip, about a tenth of a second, and late is the harmless direction —
    the report's highlight then trails the picture by that much instead of leading it.
    """
    try:
        answer = run(adb.await_file_bytes_cmd(serial, device_path, timeout))
    except (subprocess.CalledProcessError, OSError) as exc:
        _logger.debug(
            "waiting for screenrecord's first bytes on %s failed (%s); falling back to its pid",
            serial,
            exc,
        )
        return None
    # The last line, not the whole output: an image can print a banner ahead of the command's own.
    lines = answer.strip().splitlines()
    if lines and lines[-1].strip() == "1":
        return time.monotonic()
    # Disclosed like the sibling waits' own give-ups: without a line here, a run whose anchor fell
    # back to the weaker pid signal looks identical to one that never needed the fallback.
    _logger.warning(
        "screenrecord on %s wrote no bytes within %ss; its start is confirmed by the device-side "
        "process instead of its first bytes",
        serial,
        timeout,
    )
    return None


def _await_screenrecord_started(
    serial: str,
    run: adb.RunFn,
    baseline_pids: frozenset[str],
    timeout: float = _VIDEO_START_TIMEOUT,
    poll: float = _SCREENRECORD_PID_POLL,
) -> float | None:
    """Wait until the device-side `screenrecord` process exists, not merely spawned locally.

    The mirror of `_await_screenrecord_stopped`: a *new* pid (not already present in
    `baseline_pids`, captured before this attempt spawned) means the process is running
    device-side. This confirms less than the iOS video signal does (a process existing is not proof
    the encoder is yet emitting frames), but it is still a real, earlier signal than the moment the
    local `adb shell` client returned, and it lands before the app launches either way. The baseline
    guards a crash-retry (BE-0049) or any other leaked `screenrecord` still running on the same
    device: without it, that unrelated process's pid would confirm a start that never happened.
    Poll to a bounded deadline (a condition wait, not a fixed sleep); a probe failure is retried like
    any other unmet condition rather than aborting the wait early, since — unlike
    `_await_screenrecord_stopped` — nothing here needs to avoid hanging the pull. Give up and warn
    only once the deadline itself is reached, rather than guess a start time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Stamped *before* the probe: `adb shell pgrep` is a full round trip, so reading the clock
        # after it returns charges that latency to the recording's start and biases the video anchor
        # late — seeking early on every derived offset, the same direction as the drift this
        # correction removes.
        probed_at = time.monotonic()
        try:
            current = {pid for pid in run(adb.screenrecord_pids_cmd(serial)).split() if pid}
        except (subprocess.CalledProcessError, OSError) as exc:
            _logger.debug(
                "transient probe failure while confirming screenrecord start on %s (%s); retrying",
                serial,
                exc,
            )
            time.sleep(poll)
            continue
        if current - baseline_pids:
            return probed_at
        time.sleep(poll)
    _logger.warning(
        "device-side screenrecord on %s did not appear within %ss; %s",
        serial,
        timeout,
        _ANCHOR_UNCORRECTED_MSG,
    )
    return None


def _screenrecord_file_size(serial: str, run: adb.RunFn, device_path: str) -> int:
    """The device-side recording's current size, or 0 when nothing could be read.

    0 is also the honest answer for the ordinary case (the file does not exist until the first byte
    lands), so a probe failure is indistinguishable from it and is disclosed by the one caller that
    needs the distinction — the pre-spawn baseline, whose whole job is to not be fooled by an
    earlier attempt's leftover bytes.
    """
    try:
        return adb.parse_file_size(run(adb.file_size_cmd(serial, device_path))) or 0
    except (subprocess.CalledProcessError, OSError):
        return 0


def _screenrecord_baseline_size(serial: str, run: adb.RunFn, device_path: str) -> int:
    """The recording file's size *before* this spawn, so leftover bytes can't confirm a start."""
    try:
        size = adb.parse_file_size(run(adb.file_size_cmd(serial, device_path)))
    except (subprocess.CalledProcessError, OSError) as exc:
        # A 0 from a failed probe reads as "no leftover bytes", silently disabling the stale-retry
        # guard the baseline exists for, so the failure is disclosed rather than swallowed.
        _logger.warning(
            "could not size %s on %s before spawning screenrecord (%s); a finalized earlier "
            "attempt's leftover bytes may now confirm growth that never happened",
            device_path,
            serial,
            exc,
        )
        return 0
    return size or 0


def _await_screenrecord_growing(
    serial: str,
    run: adb.RunFn,
    device_path: str,
    baseline_size: int,
    timeout: float = _VIDEO_START_TIMEOUT,
    poll: float = _SCREENRECORD_GROWTH_POLL,
) -> bool:
    """Wait until the device-side recording grows past `baseline_size`, confirming it emits frames.

    The Android-only growth check (BE-0367); iOS has no twin, because `simctl io recordVideo` leaves
    its mp4 at zero bytes until finalize and answers the same question on stderr. `screenrecord`'s
    process existing — all `_await_screenrecord_started` can see — is not proof the encoder is
    producing: a wedged renderer leaves the process alive and the file empty, and that is the
    difference between a slow run and a stalled one. Poll to a bounded deadline (a condition wait,
    not a fixed sleep); a probe failure is retried like any other unmet condition, since a stalled
    device is exactly where the probe itself is most likely to fail transiently.

    The healthy case costs exactly one probe, because a producing recording has already passed its
    baseline by the time the first one lands. Only a stall pays the rest, and `_SCREENRECORD_GROWTH_POLL`
    keeps even that bounded (see its comment for why this poll is coarser than its two siblings).

    Returns:
        Whether growth was confirmed before the deadline. False is a stall signal the caller acts
        on, never a run-ending failure — the recording continues either way.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _screenrecord_file_size(serial, run, device_path) > baseline_size:
            return True
        time.sleep(poll)
    return False


def _confirm_screenrecord_growing(
    serial: str, run: adb.RunFn, device_path: str, baseline_size: int
) -> None:
    """Check the recording is producing bytes; on no growth, warn and capture the stall's state.

    Deliberately observational: it leaves `true_start` and `start_confirmed` exactly as the pid
    confirmation settled them. Whether "the encoder is producing nothing" should also drive the
    BE-0354 recovery rung is a recovery-semantics question, not a diagnostics one, so BE-0367
    records the condition and changes no verdict or recovery behavior.
    """
    if _await_screenrecord_growing(serial, run, device_path, baseline_size, _video_start_timeout()):
        return
    _logger.warning(
        "screenrecord on %s produced no new bytes in %s within %ss; the recording may be empty "
        "and the device's renderer wedged",
        serial,
        device_path,
        _video_start_timeout(),
    )
    stall_diagnostics.capture("screenrecord-no-growth", stall_diagnostics.device_probes(serial))


def start_screenrecord(
    serial: str,
    path: Path,
    spawn: Spawn = spawn,
    run: adb.RunFn = adb.real_run,
    *,
    time_limit: int | None = None,
    size: str | None = None,
    bit_rate: int | None = None,
    confirm_started: bool = False,
) -> Interval:
    """Record the Android screen; stop() (SIGINT) finalizes the mp4, then pulls it off the device.

    `screenrecord` writes device-side (it cannot stream to a host file), so recording is a running
    process plus a post-stop transform: wait for the device-side finalize, pull the mp4 to `path`,
    then remove it device-side. SIGINT (not a kill) lets `screenrecord` flush a complete mp4, the same
    reason simctl recordVideo finalizes on SIGINT. Two waits guard the finalize: `stop()` gives the
    *local* `adb shell` client `_VIDEO_FINALIZE_TIMEOUT` before any hard kill, then the transform waits
    for the *device-side* `screenrecord` to exit (`_await_screenrecord_stopped`) — the local client
    returns before the device finishes writing the moov atom, so pulling without that wait races the
    finalize into a truncated, unplayable file. `confirm_started`, when set, clears the device-side
    path and waits for the recording's first bytes, so the returned `Interval.true_start` is the
    moment the muxer started on the first frame — the anchor the report seeks against, since this
    recorder's own duration ends at its last frame rather than at the stop (`measure_origin`). Where
    that wait cannot answer, it polls for the device-side process instead, sharing the same deadline
    (a weaker signal, but still real and earlier than the local client merely returning), and the
    finished file's duration stays available to outrank that pid instant.

    `time_limit`/`size`/`bit_rate` forward to `adb.screenrecord_cmd` (see its docstring) for a caller
    whose recording window and artifact-size budget need bounding, e.g. an install+test window run
    outside `bajutsu run`.
    """
    device_path = adb.VIDEO_DEVICE_PATH
    # Captured before spawning: a leaked screenrecord from a crash-retry (BE-0049) or any other
    # stale process on the same device must not confirm a start that never happened. Clearing the
    # target makes the first-bytes wait below trustworthy; where the clear failed, the size baseline
    # guards the growth check the same way — a leftover mp4 from a finalized earlier attempt
    # already has bytes.
    baseline_pids = frozenset(_screenrecord_pids(serial, run)) if confirm_started else frozenset()
    cleared = confirm_started and _clear_screenrecord_target(serial, run, device_path)
    baseline_size = (
        _screenrecord_baseline_size(serial, run, device_path)
        if confirm_started and not cleared
        else 0
    )
    spawned_at = time.monotonic()
    proc = spawn(
        adb.screenrecord_cmd(
            serial, device_path, time_limit=time_limit, size=size, bit_rate=bit_rate
        ),
        None,
    )
    # Resolved per call for the same reason as `start_video`'s (BE-0348). The first bytes are the
    # anchor: this recorder's own duration cannot place its origin exactly (`measure_origin` below),
    # so this instant is what every seek offset in the report is measured from. They also answer the
    # growth question outright, so a recording confirmed this way skips that probe.
    #
    # One deadline covers both start waits, so a slow device that times out the first-bytes wait
    # pays no second full timeout for the pid before the app even launches. The pid still gets one
    # probe: whether the process exists is what BE-0354's recovery rung reads, and a spent budget
    # must not turn a live recording into an unconfirmed one.
    deadline = time.monotonic() + _video_start_timeout()
    first_bytes_at = (
        _await_screenrecord_first_bytes(serial, run, device_path, _video_start_timeout())
        if cleared
        else None
    )
    true_start = first_bytes_at
    if confirm_started and true_start is None:
        remaining = max(_SCREENRECORD_PID_POLL, deadline - time.monotonic())
        true_start = _await_screenrecord_started(serial, run, baseline_pids, remaining)
        # Only worth asking once the process is known to exist: with no process there is nothing
        # to produce bytes, that path already warned, and a second full timeout would buy no new
        # fact. This one separates a live-but-producing-nothing recording — a wedged renderer —
        # from a healthy one, and is the second of BE-0367's two stall triggers.
        if true_start is not None:
            _confirm_screenrecord_growing(serial, run, device_path, baseline_size)

    def transform(target: Path) -> Path:
        # The local `adb shell` has returned, but the device-side screenrecord is still finalizing;
        # wait for it to exit so the pull gets a complete mp4 rather than a moov-less truncation.
        _await_screenrecord_stopped(serial, run)
        # Let a failed pull surface (like the iOS video provider): swallowing it would record a video
        # artifact path with no file behind it, turning a real problem into a silent one.
        run(adb.pull_cmd(serial, device_path, str(target)))
        # The recording is pulled; a failed cleanup of the device copy must not fail the run.
        with contextlib.suppress(subprocess.CalledProcessError, OSError):
            run(adb.rm_cmd(serial, device_path))
        return target

    return Interval(
        kind="video",
        path=path,
        provider=ADB_PROVIDER,
        true_start=true_start,
        start_confirmed=(true_start is not None) if confirm_started else None,
        spawned_at=spawned_at,
        # Only the first-byte anchor outranks the end-based measurement; a pid instant stamped after
        # a timed-out wait does not, so the measurement stays on for that fallback.
        measure_origin=first_bytes_at is None,
        _proc=proc,
        _stop_signal=signal.SIGINT,
        _stop_timeout=_VIDEO_FINALIZE_TIMEOUT,
        _transform=transform,
    )


def start_logcat(serial: str, path: Path, spawn: Spawn = spawn) -> Interval:
    """Begin streaming `adb logcat` to `path`; stop() (SIGTERM) ends the stream (the deviceLog twin)."""
    proc = spawn(adb.logcat_cmd(serial), path)
    return Interval(
        kind="deviceLog", path=path, provider=ADB_PROVIDER, _proc=proc, _stop_signal=signal.SIGTERM
    )


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f%z")
    except ValueError:
        return None


def parse_app_trace(ndjson_text: str) -> list[dict[str, object]]:
    """Pair '<name> started' / '<name> finished' log lines into timed intervals."""
    begins: dict[str, datetime] = {}
    out: list[dict[str, object]] = []
    for raw in ndjson_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("eventType") != "logEvent":
            continue
        message = str(event.get("eventMessage") or "").strip()
        stamp = _parse_ts(event.get("timestamp"))
        if stamp is None:
            continue
        begin = _BEGIN.match(message)
        if begin:
            begins[begin.group("name")] = stamp
            continue
        end = _END.match(message)
        if end and end.group("name") in begins:
            start = begins.pop(end.group("name"))
            out.append(
                {
                    "name": end.group("name"),
                    "begin": start.isoformat(),
                    "end": stamp.isoformat(),
                    "durationMs": round((stamp - start).total_seconds() * 1000, 1),
                }
            )
    return out


def start_app_trace(
    udid: str, raw_path: Path, json_path: Path, subsystem: str, spawn: Spawn = spawn
) -> Interval:
    """Stream the app's logs to raw_path; on stop, write the parsed trace to json_path."""
    proc = spawn(app_trace_cmd(udid, subsystem), raw_path)

    def transform(raw: Path) -> Path:
        text = raw.read_text(encoding="utf-8", errors="ignore") if raw.exists() else ""
        json_path.write_text(json.dumps(parse_app_trace(text), indent=2), encoding="utf-8")
        return json_path

    return Interval(
        kind="appTrace",
        path=raw_path,
        _proc=proc,
        _stop_signal=signal.SIGTERM,
        _transform=transform,
    )
