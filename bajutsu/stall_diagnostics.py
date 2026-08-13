"""Capture the host and Simulator state a stall destroys, bounded and best-effort (BE-0361 unit 2).

When the runner channel declares a mid-run crash, or `recordVideo` never produces a byte, the state
that says *why* — whether the Simulator's render service is wedged or the virtualized host is
starving it — exists for a moment and is gone before anyone reads the failing job. Only the running
process knows when that moment is, which is why this capture lives here rather than in CI.

Two properties bound what it may cost the failure it documents. It is **opt-in**: with
`BAJUTSU_STALL_DIAGNOSTICS` unset (the default) every entry point returns immediately, so no run
that has not asked for it pays anything. And when it is set, each capture spends at most
`_CAPTURE_BUDGET` seconds across all of its probes and a run takes at most `_MAX_CAPTURES` of them —
a crash-looping run cannot fill the disk or push its job into `timeout-minutes`.

Nothing here reaches a verdict: the probes only write files, and a probe that fails is noted and
skipped rather than raised, since a diagnostics failure must never replace the failure being
diagnosed.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from bajutsu import simctl
from bajutsu.artifact_perms import make_run_dir, restrict_file

_logger = logging.getLogger(__name__)

# Names the directory captures are written into. Unset disables the capture entirely — the hooks are
# CI's to opt into, and an ordinary local run should not shell out to `sample` on a crash.
_DIAGNOSTICS_ENV = "BAJUTSU_STALL_DIAGNOSTICS"

# Captures per process. A degrading device crashes the runner repeatedly, and the second and third
# captures still add information (is the host load rising?); past that they only repeat themselves.
_MAX_CAPTURES = 3

# Wall clock one capture may spend, across every probe it runs. The capture happens on a path that is
# already failing and already waiting out a recovery window, so it must stay small next to that
# window rather than compete with the job's own `timeout-minutes` — the very failure mode BE-0353 and
# BE-0354 exist to keep a degrading lane out of.
_CAPTURE_BUDGET = 30.0

# Per-probe ceilings, each additionally clamped to whatever is left of the budget above. The
# screenshot gets the largest: its *duration* is the datum (a `simctl` screenshot that also stalls
# says the render service is wedged, not the runner), so cutting it short at a second would answer
# nothing.
_SCREENSHOT_TIMEOUT = 15.0
_SNAPSHOT_TIMEOUT = 10.0
_SAMPLE_TIMEOUT = 15.0

# Seconds `sample` spends collecting before it symbolicates and writes.
_SAMPLE_SECONDS = 1

# The processes that serve rendering and screenshots, plus the runner's own `xcodebuild` host. Exact
# process names (`pgrep -x`), not patterns: a substring match would sweep in unrelated processes.
# The CoreSimulator service is spelled in full because that *is* its process name — `pgrep -x
# CoreSimulatorService` matches nothing, which would have left the host service most central to the
# wedge hypothesis silently unsampled.
#
# Ordered most-central-first, because `_MAX_SAMPLES` cuts the walk off from the end: the render and
# screenshot path is what the capture exists to explain, and `xcodebuild` — which is alive by
# definition whenever a run is still going — would otherwise crowd it out of every capture.
_SAMPLE_PROCESSES = (
    "com.apple.CoreSimulator.CoreSimulatorService",
    "backboardd",
    "SpringBoard",
    "testmanagerd",
    "xcodebuild",
)

# Thread samples per capture, across every process above. `sample` is the expensive probe, so the cap
# keeps one process's fan-out (several pids of one name) from starving the rest; the per-capture
# budget, not this number, is what bounds the wall clock.
_MAX_SAMPLES = len(_SAMPLE_PROCESSES)

# How much of a probe's stderr is folded into the summary — enough to identify a failure without
# turning the summary into a log.
_STDERR_EXCERPT = 400

_SUMMARY = "probe.txt"

_captures = 0


def capture(reason: str, udid: str | None = None) -> None:
    """Capture the state behind a stall into a fresh directory, if the operator asked for it.

    Returns without touching the filesystem when `BAJUTSU_STALL_DIAGNOSTICS` is unset or this run has
    already taken `_MAX_CAPTURES`. Never raises: the caller is a failure path, and a capture that
    fails must not become the failure that surfaces.

    Args:
        reason: Which trigger fired, e.g. `runner-crash`. Names the capture directory, so a run that
            stalls twice for different reasons keeps both.
        udid: The device to screenshot, when the trigger knows it. Absent, the host-side probes still
            run — they answer the starvation hypothesis on their own.
    """
    global _captures

    root = os.environ.get(_DIAGNOSTICS_ENV)
    if not root:
        return
    if _captures >= _MAX_CAPTURES:
        _logger.debug("stall diagnostics: %s ignored, already captured %s", reason, _captures)
        return
    _captures += 1

    # The pid keys the directory to this process, not just to the counter: one CI job can run two
    # `bajutsu run` processes against the same collection directory (the `bundled-runner` lane runs
    # two smoke steps), and both would otherwise write `stall-01-<reason>` and overwrite each other's
    # evidence — losing the earlier stall, which is the one that started the degradation.
    dest = Path(root) / f"stall-{_captures:02d}-{_slug(reason)}-{os.getpid()}"
    try:
        make_run_dir(dest)
    except (OSError, ValueError) as exc:
        _logger.warning("stall diagnostics: cannot create %s (%s)", dest, exc)
        return

    _logger.warning("stall diagnostics: capturing the state behind '%s' → %s", reason, dest)
    deadline = time.monotonic() + _CAPTURE_BUDGET
    if udid is not None:
        _screenshot(dest, udid, deadline)
    # Cheap snapshots before the expensive `sample`s, so the budget cannot run out with the host's
    # load — the answer to the starvation hypothesis — unrecorded.
    _probe(dest, "ps", ["/bin/ps", "aux"], _SNAPSHOT_TIMEOUT, deadline, output="ps.txt")
    _probe(dest, "vm_stat", ["/usr/bin/vm_stat"], _SNAPSHOT_TIMEOUT, deadline, output="vm_stat.txt")
    _sample_processes(dest, deadline)


def reset() -> None:
    """Forget how many captures this process has taken (the per-run cap's only mutable state)."""
    global _captures
    _captures = 0


def _slug(reason: str) -> str:
    """A filesystem-safe stem for `reason`, which reaches a path."""
    return re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "stall"


def _screenshot(dest: Path, udid: str, deadline: float) -> None:
    """Time a `simctl` screenshot. Its duration answers hypothesis (1) whether or not the png lands."""
    png = dest / "screenshot.png"
    try:
        argv = simctl.screenshot_cmd(udid, str(png))
    except (simctl.DeviceError, ValueError) as exc:
        _note(dest, f"screenshot: refused the device id ({exc})")
        return
    _probe(dest, "screenshot", argv, _SCREENSHOT_TIMEOUT, deadline, writes=png)


def _sample_processes(dest: Path, deadline: float) -> None:
    """Thread-sample the rendering and screenshot processes, up to `_MAX_SAMPLES` of them."""
    taken = 0
    for name in _SAMPLE_PROCESSES:
        for pid in _pids_of(name, dest, deadline):
            if taken >= _MAX_SAMPLES:
                _note(dest, f"sample: stopped at {_MAX_SAMPLES} processes")
                return
            taken += 1
            report = dest / f"sample-{name}-{pid}.txt"
            _probe(
                dest,
                f"sample:{name}:{pid}",
                ["/usr/bin/sample", str(pid), str(_SAMPLE_SECONDS), "-file", str(report)],
                _SAMPLE_TIMEOUT,
                deadline,
                writes=report,
            )


def _pids_of(name: str, dest: Path, deadline: float) -> list[int]:
    """The live pids of `name`, or an empty list when it isn't running (or the lookup failed)."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return []
    try:
        found = subprocess.run(
            ["/usr/bin/pgrep", "-x", name],
            capture_output=True,
            text=True,
            timeout=min(_SNAPSHOT_TIMEOUT, remaining),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _note(dest, f"pgrep {name}: {type(exc).__name__} ({exc})")
        return []
    # `pgrep` exits 1 when nothing matches, which is the ordinary case for a process this host
    # doesn't run — not worth a note.
    return [int(line) for line in found.stdout.split() if line.isdigit()]


def _probe(
    dest: Path,
    label: str,
    argv: Sequence[str],
    timeout: float,
    deadline: float,
    *,
    output: str | None = None,
    writes: Path | None = None,
) -> None:
    """Run one bounded probe, recording its exit and elapsed time in the summary. Never raises.

    Args:
        output: Name under `dest` to write the probe's own stdout into.
        writes: The file the command writes for itself (`simctl io screenshot`, `sample -file`),
            restricted afterwards like any other artifact — a device screenshot is exactly the
            sensitive kind BE-0131 locks down, and the capture directory's own mode must not be the
            only thing guarding it.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _note(dest, f"{label}: skipped, the capture budget is spent")
        return
    started = time.monotonic()
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            timeout=min(timeout, remaining),
            check=False,
        )
    except subprocess.TimeoutExpired:
        # The interesting outcome, not an error: a probe that times out is itself the evidence that
        # the service behind it is wedged.
        _note(dest, f"{label}: TIMED OUT after {time.monotonic() - started:.1f}s")
        _secure(dest, label, writes)
        return
    except (OSError, subprocess.SubprocessError) as exc:
        _note(dest, f"{label}: {type(exc).__name__} ({exc})")
        _secure(dest, label, writes)
        return
    elapsed = time.monotonic() - started
    try:
        if output is not None:
            (dest / output).write_bytes(done.stdout)
            restrict_file(dest / output)
    except OSError as exc:
        _note(dest, f"{label}: could not write {output} ({exc})")
        return
    _secure(dest, label, writes)
    stderr = done.stderr.decode(errors="replace")[:_STDERR_EXCERPT]
    # Folded onto the summary's one-line-per-probe shape: these tools narrate over several lines, and
    # a multi-line entry would read as several probes.
    collapsed = " ".join(stderr.split())
    suffix = f" — {collapsed}" if collapsed else ""
    _note(dest, f"{label}: exit {done.returncode} in {elapsed:.1f}s{suffix}")


def _secure(dest: Path, label: str, writes: Path | None) -> None:
    """Restrict a file the probe's own command wrote, on every path out of `_probe`.

    Called from the failure paths too, not only after a clean exit: a killed `simctl io screenshot`
    can already have written the frame, and a device screenshot is exactly the artifact BE-0131 locks
    to owner-only. `restrict_file` raises when the file vanishes underneath it, and that exception
    would escape `capture` — which the video trigger calls bare — so the `chmod` stays guarded.
    """
    if writes is None:
        return
    try:
        restrict_file(writes)
    except OSError as exc:
        _note(dest, f"{label}: could not secure {writes.name} ({exc})")


def _note(dest: Path, line: str) -> None:
    """Append one outcome line to the capture's summary; a summary that cannot be written is dropped."""
    try:
        with (dest / _SUMMARY).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        restrict_file(dest / _SUMMARY)
    except OSError as exc:
        _logger.debug("stall diagnostics: could not record '%s' (%s)", line, exc)
