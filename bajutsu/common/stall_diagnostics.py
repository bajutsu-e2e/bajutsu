"""Capture the state a stall destroys, bounded and best-effort (BE-0361 unit 2, BE-0367 unit 2).

When a backend stalls — the XCUITest runner channel declares a mid-run crash, `recordVideo` never
produces a byte, the adb resident read channel dies, `screenrecord` runs without emitting one — the
state that says *why* exists for a moment and is gone before anyone reads the failing job. Whether
the device's render service is wedged or the host is starving it is the question, and only the
running process knows when the moment to ask it is, which is why this capture lives here rather than
in CI.

The two backends split cleanly along one seam. *When* to capture, what a capture may cost, and where
it lands are identical for both, so `capture` owns them. *What to read* is not: a Simulator answers
to `simctl` and a macOS host to `vm_stat`, an emulator answers to `adb` and a Linux host to `top`,
and the two share no command. Each backend therefore contributes a `ProbeSet` — `simulator_probes`
and `device_probes` below — and `capture` runs whichever one the trigger hands it. A third backend
adds a `ProbeSet` and nothing else.

Two properties bound what it may cost the failure it documents. It is **opt-in**: with
`BAJUTSU_STALL_DIAGNOSTICS` unset (the default) every entry point returns immediately, so no run
that has not asked for it pays anything. And when it is set, each capture spends at most
`_CAPTURE_BUDGET` seconds across all of its probes and each trigger takes at most
`_MAX_CAPTURES_PER_REASON` of them — a crash-looping run cannot fill the disk or push its job into
`timeout-minutes`.

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bajutsu.common.backend_cli import adb, simctl
from bajutsu.common.run_meta.artifact_perms import make_run_dir, restrict_file

_logger = logging.getLogger(__name__)

# Names the directory captures are written into. Unset disables the capture entirely — the hooks are
# CI's to opt into, and an ordinary local run should not shell out to `sample` on a crash.
_DIAGNOSTICS_ENV = "BAJUTSU_STALL_DIAGNOSTICS"

# Captures per trigger, per *process*. Budgeted per trigger rather than globally, because the two
# triggers do not fire at comparable rates: `recordVideo produced no new bytes` fires on every
# scenario of these runners — green runs included — and a job runs a dozen scenarios in one process,
# so a single shared cap would be spent on the third scenario's video warning and the runner crash
# that the whole capture exists to explain would arrive to find nothing left. In CI per-process equals
# per run (a run is one `bajutsu run` process, and the variable is armed nowhere else); a long-lived
# process such as `bajutsu serve` would consume the budget once, which `reset()` undoes.
#
# Two per trigger: a degrading device stalls repeatedly, and the second capture still adds information
# (is the host load rising?), while a third mostly repeats it.
_MAX_CAPTURES_PER_REASON = 2

# Wall clock one capture may spend, across every probe it runs. Kept small because a capture is charged
# to two clocks it does not own. BE-0353's `CrashRecoveryBudget` is a deadline set at the *first* crash
# and re-checked at each later one, so every second spent here is a second the recovery no longer has:
# a run on 2026-08-13 missed that 300s deadline by 75s with two captures of up to 30s inside the window,
# which is a diagnostic turning a recoverable degradation into a failed run. The video trigger is worse
# placed still — it fires from the scenario's own evidence setup, on green runs included. 10s leaves
# room for the screenshot (whose duration is the datum) plus the two cheap snapshots, and the deadline
# truncates the `sample` walk with a note rather than paying for it.
_CAPTURE_BUDGET = 10.0

# Per-probe ceilings, each additionally clamped to whatever is left of the budget above. The
# screenshot gets the largest: its *duration* is the datum (a `simctl` screenshot that also stalls
# says the render service is wedged, not the runner). 5s is enough to establish that — the runner's own
# read timeout is 15s, so a `simctl` path still unanswered at 5s has already told us the render service
# is not answering, and waiting the remaining 10s only spends someone else's budget to learn it twice.
_SCREENSHOT_TIMEOUT = 5.0
_SNAPSHOT_TIMEOUT = 10.0
_SAMPLE_TIMEOUT = 15.0

# Ceiling for one `adb` read (BE-0367). Half a snapshot's, because the adb probes are the ones a
# wedged emulator can hold to their whole ceiling, and two of them at `_SNAPSHOT_TIMEOUT` would
# outrun the shared budget — the second would be skipped with a note, and a capture that reads the
# compositor but never the log is missing the half that says what the device was doing. Like the
# Simulator screenshot, an adb read that times out is itself the datum.
_ADB_TIMEOUT = 5.0

# Seconds `sample` spends collecting before it symbolicates and writes.
_SAMPLE_SECONDS = 1

# The processes that serve rendering and screenshots, plus the runner's own `xcodebuild` host. Exact
# process names (`pgrep -x`), not patterns: a substring match would sweep in unrelated processes.
# The CoreSimulator service is spelled in full because that *is* its process name — `pgrep -x
# CoreSimulatorService` matches nothing, which would have left the host service most central to the
# wedge hypothesis silently unsampled.
#
# Ordered most-central-first, because the capture budget is what cuts the walk off from the end: once
# the deadline is spent every remaining `sample` is skipped with a note, and on the interesting case —
# a wedged screenshot spending its whole ceiling — that is most of the walk. The render and screenshot
# path is what the capture exists to explain, so `xcodebuild`, alive by definition whenever a run is
# still going, is the name that should be dropped when something has to be.
_SAMPLE_PROCESSES = (
    "com.apple.CoreSimulator.CoreSimulatorService",
    "backboardd",
    "SpringBoard",
    "testmanagerd",
    "xcodebuild",
)

# Pids sampled per process name. One name resolves to several pids whenever the host has more than
# one Simulator booted — each device runs its own `backboardd` and `SpringBoard` — and without this
# those two names alone would spend the whole allowance, leaving `testmanagerd` (central to the
# XCTest-host wedge) unsampled. The global cap below is the outer bound; this is the fairness one.
_MAX_PIDS_PER_PROCESS = 2

# Thread samples per capture, across every process above — a backstop, not the rule that binds today:
# the per-name slice already holds the walk to exactly this product, so what this catches is a later
# change that widens the fan-out (a third pid per name, an unbounded pid list). The per-capture budget,
# not this number, is what bounds the wall clock.
_MAX_SAMPLES = len(_SAMPLE_PROCESSES) * _MAX_PIDS_PER_PROCESS

# How much of a probe's stderr is folded into the summary — enough to identify a failure without
# turning the summary into a log.
_STDERR_EXCERPT = 400

_SUMMARY = "probe.txt"

_captures: dict[str, int] = {}

# Failures already warned about, once per process — currently only an unwritable summary (see `_note`).
# A set of keys rather than a rebound flag, so every piece of this module's process-scoped state lives
# in a mutable container next to `_captures` and `reset()` clears all of it in one place.
_warned: set[str] = set()


@dataclass(frozen=True)
class Capture:
    """One capture in progress: where its files land, and how much clock its probes have left.

    The object handed across the backend seam, so a `ProbeSet` names what to read and nothing else.
    The destination, the shared deadline, the summary line every probe leaves behind, and the promise
    that a probe's failure is recorded rather than raised all stay on this side of the seam.
    """

    dest: Path
    deadline: float

    def probe(
        self,
        label: str,
        argv: Sequence[str],
        timeout: float,
        *,
        output: Path | None = None,
        writes: Path | None = None,
    ) -> None:
        """Run one bounded probe, recording its exit and elapsed time in the summary. Never raises."""
        _probe(self.dest, label, argv, timeout, self.deadline, output=output, writes=writes)

    def note(self, line: str) -> None:
        """Append one outcome line to this capture's summary."""
        _note(self.dest, line)

    def path(self, name: str) -> Path:
        """A file inside this capture, for a probe whose output is written rather than piped."""
        return self.dest / name


# A backend's half of a capture: the probes that answer "wedged device or starved host?" for the
# platform this run drives. Built by `simulator_probes` / `device_probes` below and passed to
# `capture`, which owns everything the two backends share. A `Callable` rather than a class, matching
# how the rest of the codebase injects behavior (`RunFn`, `ActFn`, `Spawn`): a probe set is one
# operation, and the state it needs — a udid, a serial — is closed over when it is built.
ProbeSet = Callable[[Capture], None]


def simulator_probes(udid: str | None = None) -> ProbeSet:
    """The probes that separate a wedged Simulator from a starving macOS host (BE-0361).

    Args:
        udid: The device to screenshot, when the trigger knows it. Absent, the host-side probes still
            run — they answer the starvation hypothesis on their own.
    """

    def probes(capture: Capture) -> None:
        # Sub-second snapshots first, then the screenshot, then the expensive `sample`s. Ordering by
        # cost matters on the interesting case: a wedged render service makes the screenshot spend
        # its whole ceiling, which is half the budget, and the host's load — the answer to the
        # starvation hypothesis — must already be on record by then rather than lost to the probe
        # that outran it.
        capture.probe("ps", ["/bin/ps", "aux"], _SNAPSHOT_TIMEOUT, output=capture.path("ps.txt"))
        capture.probe(
            "vm_stat", ["/usr/bin/vm_stat"], _SNAPSHOT_TIMEOUT, output=capture.path("vm_stat.txt")
        )
        if udid is not None:
            _screenshot(capture, udid)
        _sample_processes(capture)

    return probes


def device_probes(serial: str) -> ProbeSet:
    """The probes that separate a wedged emulator from a starving Linux host (BE-0367).

    The adb twin of `simulator_probes`, and deliberately not a variant of it: `simctl`, `vm_stat`,
    and `sample` have no counterpart in `adb`, `top`, and `dumpsys`, so a single probe set covering
    both would be a branch on the backend rather than shared logic.

    Args:
        serial: The device the stalled read or recording was addressing.
    """

    def probes(capture: Capture) -> None:
        # Host snapshots first, for the reason the Simulator set takes them first: the two `adb`
        # reads below are the ones a wedged device can hold to their whole ceiling, and the host's
        # load must be on record before that happens rather than lost to the probe that outran it.
        capture.probe("ps", ["ps", "aux"], _SNAPSHOT_TIMEOUT, output=capture.path("ps.txt"))
        capture.probe("top", ["top", "-bn1"], _SNAPSHOT_TIMEOUT, output=capture.path("top.txt"))
        try:
            latency = adb.dumpsys_surfaceflinger_latency_cmd(serial)
            logcat = adb.logcat_tail_cmd(serial)
        except (adb.DeviceError, ValueError) as exc:
            # Noted rather than dropped, like the Simulator screenshot's own id guard: the host
            # snapshots above already landed, so a summary silent about the device half would read
            # as a capture that found the device healthy.
            capture.note(f"adb: refused the device serial ({exc})")
            return
        # SurfaceFlinger's per-frame latency table is the compositor's own view of recent frames, so
        # a renderer that has stopped producing shows up here and not in the host snapshots above.
        capture.probe(
            "surfaceflinger",
            latency,
            _ADB_TIMEOUT,
            output=capture.path("surfaceflinger-latency.txt"),
        )
        # The device's retained ring buffer, read once after the fact — unlike the per-scenario
        # `deviceLog` interval, it still has something to show when the process serving that stream
        # is the one that died.
        capture.probe("logcat", logcat, _ADB_TIMEOUT, output=capture.path("logcat-tail.txt"))

    return probes


def capture(reason: str, probes: ProbeSet) -> None:
    """Capture the state behind a stall into a fresh directory, if the operator asked for it.

    Returns without touching the filesystem when `BAJUTSU_STALL_DIAGNOSTICS` is unset or this process
    has already taken `_MAX_CAPTURES_PER_REASON` captures for this trigger.

    Never raises. The caller is a failure path — one of them, the video trigger, calls this from inside
    a live scenario's evidence setup — so a bug in here must cost a log line and nothing else. The
    promise is enforced once, here, rather than re-derived at each probe, and it covers the backend's
    probe set too: a `ProbeSet` that raises costs the same log line.

    Args:
        reason: Which trigger fired. Names the capture directory and carries its own budget, so a
            frequent trigger cannot starve a rare one.
        probes: The backend's own reads, from `simulator_probes` or `device_probes`.
    """
    try:
        _capture(reason, probes)
    except Exception:
        _logger.warning("stall diagnostics: the capture of '%s' failed", reason, exc_info=True)


def _capture(reason: str, probes: ProbeSet) -> None:
    root = os.environ.get(_DIAGNOSTICS_ENV)
    if not root:
        return
    slug = _slug(reason)
    taken = _captures.get(slug, 0)
    if taken >= _MAX_CAPTURES_PER_REASON:
        # A warning, not a debug line: the iOS lane installs no log handler, so a debug line is no
        # record at all — and "the stall was not captured" is exactly what the operator reading the
        # artifact needs to know before concluding the evidence says nothing.
        _logger.warning(
            "stall diagnostics: NOT capturing '%s' — this process already took %s for that trigger",
            reason,
            taken,
        )
        return
    # Counted before the directory is attempted, so a stream of unusable destinations spends the budget
    # rather than retrying the same broken path on every stall of a crash-looping run.
    _captures[slug] = taken + 1

    # The pid keys the directory to this process, not just to the counter: one CI job can run two
    # `bajutsu run` processes against the same collection directory (the `bundled-runner` lane runs
    # two smoke steps), and both would otherwise write `stall-01-<reason>` and overwrite each other's
    # evidence — losing the earlier stall, which is the one that started the degradation.
    dest = Path(root) / f"stall-{_captures[slug]:02d}-{slug}-{os.getpid()}"
    try:
        make_run_dir(dest)
    except (OSError, ValueError) as exc:
        _logger.warning("stall diagnostics: cannot create %s (%s)", dest, exc)
        return

    _logger.warning("stall diagnostics: capturing the state behind '%s' → %s", reason, dest)
    # An absolute stamp, because the whole point of the three layers is correlation: the host telemetry
    # and the unified-log extracts are wall-clock stamped, and without this the only thing tying a
    # capture to a moment in them is a directory mtime that may not survive artifact upload.
    _note(dest, f"captured at {datetime.now(UTC).isoformat()} — reason={reason}")
    probes(Capture(dest=dest, deadline=time.monotonic() + _CAPTURE_BUDGET))


def reset() -> None:
    """Forget this process's state: how many captures each trigger took, and what it has warned about."""
    _captures.clear()
    _warned.clear()


def _slug(reason: str) -> str:
    """A filesystem-safe stem for `reason`, which reaches a path."""
    return re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "stall"


def _screenshot(capture: Capture, udid: str) -> None:
    """Time a `simctl` screenshot. Its duration answers hypothesis (1) whether or not the png lands."""
    png = capture.path("screenshot.png")
    try:
        argv = simctl.screenshot_cmd(udid, str(png))
    except (simctl.DeviceError, ValueError) as exc:
        capture.note(f"screenshot: refused the device id ({exc})")
        return
    capture.probe("screenshot", argv, _SCREENSHOT_TIMEOUT, writes=png)


def _sample_processes(capture: Capture) -> None:
    """Thread-sample the rendering and screenshot processes, bounded per name and in total."""
    taken = 0
    for name in _SAMPLE_PROCESSES:
        # Sliced per name so a multi-device host's several `backboardd`s cannot spend the whole
        # allowance before the later names are reached.
        for pid in _pids_of(name, capture)[:_MAX_PIDS_PER_PROCESS]:
            if taken >= _MAX_SAMPLES:
                capture.note(f"sample: stopped at {_MAX_SAMPLES} samples")
                return
            taken += 1
            report = capture.path(f"sample-{name}-{pid}.txt")
            capture.probe(
                f"sample:{name}:{pid}",
                ["/usr/bin/sample", str(pid), str(_SAMPLE_SECONDS), "-file", str(report)],
                _SAMPLE_TIMEOUT,
                writes=report,
            )


def _pids_of(name: str, capture: Capture) -> list[int]:
    """The live pids of `name`, or an empty list when it isn't running (or the lookup failed)."""
    remaining = capture.deadline - time.monotonic()
    if remaining <= 0:
        # Noted, unlike a `pgrep` that simply found nothing: "the budget ran out before we looked" and
        # "this process is not running" are different answers, and a summary that conflated them would
        # read as a complete capture.
        capture.note(f"pgrep {name}: skipped, the capture budget is spent")
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
        capture.note(f"pgrep {name}: {type(exc).__name__} ({exc})")
        return []
    # `pgrep` exits 1 when nothing matches and 2/3 on a usage or fatal error. Reading only stdout would
    # make an error indistinguishable from "not running" and leave the process unsampled with no note.
    if found.returncode not in (0, 1):
        capture.note(
            f"pgrep {name}: exit {found.returncode} — the lookup failed, so it was not sampled"
        )
        return []
    pids = [int(line) for line in found.stdout.split() if line.isdigit()]
    if not pids:
        # An absent render process is a *finding*, not an empty result: a wedged Simulator whose
        # `backboardd` has died is the diagnosis for hypothesis (1), and omitting the line would leave
        # "dead" indistinguishable from "never probed".
        capture.note(f"sample:{name}: NOT RUNNING")
    return pids


def _probe(
    dest: Path,
    label: str,
    argv: Sequence[str],
    timeout: float,
    deadline: float,
    *,
    output: Path | None = None,
    writes: Path | None = None,
) -> None:
    """Run one bounded probe, recording its exit and elapsed time in the summary. Never raises.

    Exactly one of `output` / `writes` is given: a probe either hands us its stdout or writes its own
    file, never both.

    Args:
        output: Where to write the probe's own stdout.
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
            output.write_bytes(done.stdout)
            restrict_file(output)
    except OSError as exc:
        _note(dest, f"{label}: could not write the probe's output ({exc})")
        _secure(dest, label, writes)
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
    to owner-only. The `chmod` stays guarded because it can still fail on a file that is present — a
    permission error, or the file replaced between the probe and the mode change — and that exception
    would otherwise escape `capture`, which the video trigger calls bare.
    """
    if writes is None:
        return
    try:
        restrict_file(writes)
    except OSError as exc:
        _note(dest, f"{label}: could not secure {writes.name} ({exc})")


def _note(dest: Path, line: str) -> None:
    """Append one outcome line to the capture's summary, warning once if the summary is unwritable."""
    try:
        with (dest / _SUMMARY).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        restrict_file(dest / _SUMMARY)
    except OSError as exc:
        # Warned rather than dropped at debug — an unwritable summary loses *every* probe outcome, and
        # a full disk on a degrading job is exactly when that happens. Once per process: the same
        # failure repeats for every probe, and a log flooded with it hides the crash underneath.
        if _SUMMARY not in _warned:
            _warned.add(_SUMMARY)
            _logger.warning("stall diagnostics: cannot write %s (%s)", dest / _SUMMARY, exc)
