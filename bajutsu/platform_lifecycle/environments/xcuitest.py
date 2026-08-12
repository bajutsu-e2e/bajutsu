"""The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator, or the same
runner without simctl prep on a real device (BE-0019, real-device targeting BE-0238).

This module also isolates the `.xctestrun` packaging helpers (`_patch_xctestrun_env`) and their
`plistlib` / `tempfile` / `shlex` imports, which only XCUITest needs, out of the environment modules
every platform loads.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import plistlib
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal, cast

from bajutsu import backends, simctl
from bajutsu.config import Effective, XcuitestConfig, require_ios
from bajutsu.drivers import base
from bajutsu.platform_lifecycle.environments._bundled_runner import (
    bundled_products_dir,
    bundled_runner_build_info,
    materialize,
)
from bajutsu.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.scenario import Preconditions

_logger = logging.getLogger(__name__)

# Overrides the directory the runner subprocess's combined stdout/stderr is captured into, one file
# per cold spawn. Capture is on by default (BE-0319 unit 1): a startup failure or mid-run crash is
# diagnosable without a human pre-arming this, so the variable now only redirects the capture
# directory. A default (env-unset) capture goes to `_DEFAULT_RUNNER_LOG_DIR` and is pruned on
# teardown; an explicit directory is kept, since the operator asked for it.
_RUNNER_LOG_ENV = "BAJUTSU_XCUITEST_RUNNER_LOG"

# Where a default (env-unset) capture goes — a run-scoped temporary area teardown can prune, so a
# passing run leaves nothing behind while a failing one is still diagnosable.
_DEFAULT_RUNNER_LOG_DIR = Path(tempfile.gettempdir()) / "bajutsu-xcuitest-runner"

# Lines of captured runner output to fold into the crash warning and the startup-failure error —
# enough to show the tail of an `xcodebuild` failure without dumping the whole (verbose) log.
_RUNNER_LOG_TAIL_LINES = 20

# A cold XCTest-host launch that never binds its port is a transient blip (the class BE-0207 absorbs
# at the transport layer). One retry absorbs a one-off cold-start blip; a repeatable failure — a
# broken build, signature, or app — fails every attempt and still stops the gate (BE-0049). Bounded
# to a single retry: two attempts total. The startup ceiling is shared across the attempts by default
# (`_spawn_cold_with_retry`) — a slow "health never ready" attempt has spent it, and a second full
# wait against an unchanged device would double the worst case for no new information — but a retry
# that follows a device repair restarts the ceiling, since the device it spawns onto has demonstrably
# come back up.
_COLD_SPAWN_ATTEMPTS = 2

# Between health probes during the cold-spawn wait, re-check the `xcodebuild` handle this often — a
# condition wait (no fixed sleep that ignores the condition), matching the driver's own /health poll.
_COLD_POLL_SECONDS = 0.1

# The captured lines that say the XCTest run reached its end, so this runner will never bind its
# port. `xcodebuild` outlives its own test run by a long way: when the app launch itself times out,
# the suite can report failure long before the process exits, so the liveness check (which watches
# the *process*) sees nothing and the wait runs out the whole ceiling for no reason — exactly the
# failure BE-0319's retry exists to absorb, and the one it could never reach without this marker.
# Reading the terminal marker out of the capture ends the wait as soon as the run actually ended.
# Both outcomes end the run — a suite that passed has exited too — so neither can be a runner still
# on its way up.
_RUN_ENDED_MARKERS = (b"Test Suite 'All tests' failed", b"Test Suite 'All tests' passed")

# `XCUIApplication.launch()` giving up on the app under test — the dominant CI signature, and the one
# that says the *device* is degraded rather than the build broken. Read for the diagnostic only: the
# recovery ladder keys on the failure *kind* (any run-ended attempt reboots, marker or not), not on
# this text, which precedes the `_RUN_ENDED_MARKERS` line that actually ends the wait.
_LAUNCH_TIMEOUT_MARKER = b"Timed out attempting to launch"

# Carried between probes so a marker split across two reads is still matched; one byte short of the
# longest marker is all the overlap that can hide one.
_RUN_ENDED_OVERLAP = max(len(m) for m in (*_RUN_ENDED_MARKERS, _LAUNCH_TIMEOUT_MARKER)) - 1


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the runner's process group, then SIGKILL whatever is left of it; never raises.

    `xcodebuild` spawns the XCTest-host plumbing that drives the device, so signalling only the
    parent leaves those children alive and holding the device's automation session — the state a
    following spawn attempt then has to spawn onto. The runner gets its own process group
    (`start_new_session` in `_spawn_runner`) precisely so this can reach all of it, and every step is
    suppressed: a discard runs on the failure path, where raising would mask the real error.

    The closing SIGKILL is unconditional rather than an escalation the parent's exit can skip, because
    the parent's exit says nothing about its children: `xcodebuild` can unwind promptly on the SIGTERM
    while an XCTest-host child ignores it and keeps the automation session, which is exactly what this
    exists to prevent. The group id is read once while the leader is alive, since reaping the leader
    makes it unreadable; sweeping a group that is already empty raises `ProcessLookupError`, which is
    suppressed along with the rest.
    """
    try:
        pgid: int | None = os.getpgid(proc.pid)
    except OSError:
        pgid = None  # already reaped, or no group to read — fall back to the process itself

    def signal_group(sig: int) -> None:
        """Signal the whole group, falling back to the process alone when the group is unreachable."""
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
                return
            except OSError:
                # Gone, not ours, or unsignallable for any other reason — every case is a group this
                # signal did not reach, so all of them fall through to the process itself. Narrowing
                # this to the expected errors would let an unexpected one leave the runner alive.
                pass
        with contextlib.suppress(OSError):
            proc.terminate() if sig == signal.SIGTERM else proc.kill()

    signal_group(signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout=5)
    signal_group(signal.SIGKILL)
    # Reap the parent so it does not linger as a zombie until this process exits.
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout=5)


def _allocate_port() -> int:
    """Bind an ephemeral port on localhost and return it.

    The socket is closed immediately so the runner can bind it; the window for another process to
    grab the port is negligible on localhost.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


# Cold `xcodebuild test-without-building` startup (XCTest host boot + app launch before the runner's
# server answers /health) routinely exceeds the driver's 10s default on a loaded CI runner; a warm
# start still returns as soon as /health is ready, so this only raises the ceiling for the cold case.
# Overridable per lane so a contended CI host can extend the ceiling without a code change.
_RUNNER_STARTUP_TIMEOUT = 120.0
_RUNNER_STARTUP_TIMEOUT_ENV = "BAJUTSU_XCUITEST_STARTUP_TIMEOUT"


def _runner_startup_timeout() -> float:
    """The cold-runner startup ceiling in seconds, from the env override or the default."""
    raw = os.environ.get(_RUNNER_STARTUP_TIMEOUT_ENV)
    if not raw:
        return _RUNNER_STARTUP_TIMEOUT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _RUNNER_STARTUP_TIMEOUT


# A *respawn* — a cold spawn on a device this run already brought up once, because a mid-run crash
# evicted its warm resident — is not the first bring-up: the Simulator is booted and the app
# installed, so the slow parts of `_RUNNER_STARTUP_TIMEOUT` (first boot + install + the initial
# `xcodebuild test-without-building` host spin-up) are already paid. Waiting the full cold ceiling
# again on a respawn only lets a dead runner burn minutes before a crash surfaces (the between-attempt
# recovery budget in pipeline.py cannot cut a single respawn's readiness wait short). This overrides
# the ceiling for respawns only; unset keeps the cold ceiling, so a lane not opting in is unchanged.
_RESPAWN_TIMEOUT_ENV = "BAJUTSU_XCUITEST_RESPAWN_TIMEOUT"


# How long the between-attempts device recovery may spend before the run gives up on the device
# (`_check_recovery_budget`). Generous enough for the slowest rung — creating a Simulator and waiting
# out its first boot — but bounded, because a device that takes longer than this to come back is not
# coming back, and a retry funded out of the remaining job time would only fail later.
_RECOVERY_TIMEOUT = 180.0
_RECOVERY_TIMEOUT_ENV = "BAJUTSU_XCUITEST_RECOVERY_TIMEOUT"


def _recovery_timeout() -> float:
    """The device-recovery wall bound in seconds, from the env override or the default."""
    raw = os.environ.get(_RECOVERY_TIMEOUT_ENV)
    if not raw:
        return _RECOVERY_TIMEOUT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _RECOVERY_TIMEOUT


def _respawn_timeout() -> float | None:
    """The respawn readiness ceiling (s) from the env, or None (fall back to the cold ceiling) unset/invalid.

    Non-positive or unparseable reads as None (use the cold ceiling): the override only ever *tightens*
    a respawn's wait, never removes readiness waiting altogether.
    """
    raw = os.environ.get(_RESPAWN_TIMEOUT_ENV)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# Probing a *warm* runner before reuse (BE-0291): a live runner answers /health at once, so this only
# bounds the wedged case — a runner that crashed after repeated app.launch() cycles must be detected
# quickly and respawned, not waited on for the cold ceiling.
_WARM_HEALTH_TIMEOUT = 10.0

# Empirical cap, not a documented platform limit: each warm reuse re-attaches the XCTest automation
# session to a freshly launched app, and that session can destabilize after enough app.launch()
# cycles on a slower/contended host, even with the offending main-thread work in `BajutsuScreen`'s
# `viewDidAppear` hook moved off-thread (`BajutsuNet.postJSON`). This bound stays as defense-in-depth
# for that reactive case, which the BE-0291 warm probe only detects after the fact. Bounding the
# reuse count makes the respawn *proactive*: after this many warm reuses, `start` respawns the runner
# cold (a fresh XCTest session) before the next launch can tip it over, so a run never hits the
# mid-scenario crash. A cold spawn resets the count. Kept below "a handful" with headroom;
# overridable per lane for on-device tuning without a code change. 0 disables warm reuse entirely
# (always cold).
_MAX_WARM_REUSES = 3
_MAX_WARM_REUSES_ENV = "BAJUTSU_XCUITEST_MAX_WARM_REUSES"


def _max_warm_reuses() -> int:
    """The warm-reuse budget before a proactive cold respawn, from the env override or the default."""
    raw = os.environ.get(_MAX_WARM_REUSES_ENV)
    if not raw:
        return _MAX_WARM_REUSES
    try:
        return max(0, int(raw))
    except ValueError:
        return _MAX_WARM_REUSES


def _never_ended() -> str | None:
    """The neutral run-ended probe: a spawn with no capture to read can only be judged by its process."""
    return None


def _run_ended_probe(log_path: Path | None) -> Callable[[], str | None]:
    """Watch the growing capture for the marker that ends an XCTest run; the reason, or `None` while it runs.

    The companion to the process-liveness check in `_await_cold_runner`: that one catches an
    `xcodebuild` that *exits*, this one an `xcodebuild` that finished its test run and lingers. Only
    the bytes appended since the previous probe are read, so polling the capture stays cheap however
    verbose it grows.

    A run that ended because the app never came to the foreground names that in its reason
    (`_LAUNCH_TIMEOUT_MARKER`): it is the signature of a degraded Simulator, and saying so is what
    lets a reader tell "this device needs rebooting" from "this build is broken" — the recovery ladder
    itself keys on the failure *kind*, not on this text.

    The verdict **latches** (BE-0354), because two consumers pulse differently. The cold gate reads
    each window once and stops at the first marker, so an edge-triggered answer suffices for it; the
    mid-run liveness predicate (`XcuitestEnvironment._runner_alive`) is level-triggered, re-asked
    throughout each recovery episode — once when the crash is declared and then once a second while
    the recovery wait runs (BE-0360) — and an unlatched probe would answer "ended" on whichever ask
    first saw the marker and "still running" for every ask after it. Both share one probe instance per
    spawn — the marker lives in a single stream of bytes, so a second, independent instance would race
    this one for it.
    """
    if log_path is None:
        return _never_ended
    offset = 0
    carry = b""
    launch_timed_out = False
    latched: str | None = None

    def probe() -> str | None:
        nonlocal offset, carry, launch_timed_out, latched
        if latched is not None:
            return latched
        try:
            with log_path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
        except OSError:
            return None  # the capture may not exist yet on a spawn that failed before writing
        if not chunk:
            return None
        window = carry + chunk
        # Sticky: the launch timeout is logged before the suite reports failure, so the two markers
        # rarely land in the same read window.
        launch_timed_out = launch_timed_out or _LAUNCH_TIMEOUT_MARKER in window
        for marker in _RUN_ENDED_MARKERS:
            if marker in window:
                cause = " after the app launch timed out" if launch_timed_out else ""
                latched = (
                    f"the xctest run ended ({marker.decode()}){cause} "
                    "before the runner bound its port"
                )
                return latched
        carry = window[-_RUN_ENDED_OVERLAP:]
        return None

    return probe


@dataclass
class _Spawned:
    """One cold-spawn attempt's live handles, injectable so the retry seam is Simulator-free (BE-0319 unit 5).

    Callables rather than the concrete process/driver, so a test can drive `_spawn_cold_with_retry`
    with fakes: `ready` is one `/health` probe, `poll` the `xcodebuild` handle's exit code (`None`
    while alive), `run_ended` the captured output's verdict on whether the test run already finished,
    `log_tail` the captured-output trailer folded into a loud failure, and `discard` the attempt's
    teardown. `run_ended` defaults to the neutral probe so a fake that exercises only the process
    paths need not supply one.
    """

    driver: base.Driver
    ready: Callable[[], bool]
    poll: Callable[[], int | None]
    log_tail: Callable[[], str]
    discard: Callable[[], None]
    run_ended: Callable[[], str | None] = _never_ended


@dataclass(frozen=True)
class _AttemptFailure:
    """Why one cold-spawn attempt did not produce a ready runner.

    `kind` is what the recovery ladder keys on, so the choice of remedy never depends on parsing the
    prose: `process-exit` is an `xcodebuild` that gave up on its own (a fast, often transient blip),
    `run-ended` an XCTest run that finished without the runner ever serving (the app-launch timeout
    of a degraded Simulator), and `never-ready` a wait that reached its ceiling with the process
    still alive and the run still going. `detail` is the human sentence the failing error quotes.
    """

    kind: Literal["process-exit", "run-ended", "never-ready"]
    detail: str


def _await_cold_runner(
    spawned: _Spawned,
    *,
    timeout: float,
    poll: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> _AttemptFailure | None:
    """Wait for the runner to answer `/health` while watching its process; `None` if ready, else why not.

    A bounded condition wait (BE-0319 unit 3): each round probes `spawned.ready()` first — a runner
    that came up wins regardless of the other two — then `spawned.poll()`, so a runner that died
    during startup aborts at once with its exit code rather than probing a dead port for the
    remaining budget, and finally `spawned.run_ended()`, which catches the failure the exit code
    cannot: an `xcodebuild` whose test run has ended but whose process lingers (see
    `_RUN_ENDED_MARKERS`). Returns `None` once ready, else the classified failure the caller folds
    the captured tail onto and picks a recovery rung from.
    """
    deadline = clock() + timeout
    while True:
        if spawned.ready():
            return None
        exit_code = spawned.poll()
        if exit_code is not None:
            return _AttemptFailure(
                "process-exit",
                f"the xcodebuild process exited (code {exit_code}) before the runner bound its port",
            )
        ended = spawned.run_ended()
        if ended is not None:
            return _AttemptFailure("run-ended", ended)
        if clock() >= deadline:
            return _AttemptFailure("never-ready", f"health never ready within {timeout}s")
        sleep(poll)


@dataclass(frozen=True)
class _Recovery:
    """What a between-attempts recovery did, and whether the next attempt has earned a fresh budget.

    `note` goes into the failing error's diagnostics, so a reader sees which rung ran and what the
    device looked like. `fresh_budget` is the repaired device's new readiness ceiling in seconds, or
    `None` when nothing about the device changed — the difference between "retry against a device we
    just rebooted" and "retry against the same device", which is what decides whether a second full
    wait is justified.
    """

    note: str
    fresh_budget: float | None = None


def _no_recovery(failure: _AttemptFailure) -> _Recovery | None:
    """The neutral recovery: nothing to repair (a real device, or a caller that opts out)."""
    return None


def _spawn_cold_with_retry(
    spawn: Callable[[], _Spawned],
    *,
    timeout: float,
    recover: Callable[[_AttemptFailure], _Recovery | None] = _no_recovery,
    attempts: int = _COLD_SPAWN_ATTEMPTS,
    poll: float = _COLD_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> _Spawned:
    """Spawn the cold runner, await readiness with a liveness check, recover the device, and retry once.

    A cold XCTest-host launch that never binds its port is a transient infrastructure blip (BE-0207's
    class); a single retry absorbs a one-off cold-start blip, while a repeatable failure — a broken
    build, signature, or app — fails every attempt and still stops the gate, preserving BE-0049's
    "flakiness is never tolerated by absorption". Each failed attempt is discarded (no leaked
    subprocess) and its captured tail folded into the final loud `XcuitestChannelError` (unit 2), so
    the run-failing error shows *why* the runner never answered, not merely that it did not.

    Between attempts `recover` gets the classified failure and may repair the device it spawns onto:
    the retry BE-0319 added isolated every host-side resource per attempt — port, `.xctestrun`,
    capture — but never the device, so a Simulator whose app launch had just timed out was handed to
    the retry in exactly the state that had defeated the first attempt. What `recover` returns
    decides both the retry's budget and what the failing error says:

    - A `_Recovery` carrying a `fresh_budget` (the device was rebooted, or replaced outright) restarts
      the ceiling, because the next attempt runs against a device that has demonstrably come back up.
      This is what makes the dominant flake recoverable at all: an app-launch timeout ends the *first*
      attempt fast, but what it leaves behind is a degraded device, not spare seconds.
    - A `_Recovery` whose `fresh_budget` is `None` reports a rung that inspected the device and left it
      as it was, so its note reaches the diagnostics while the *shared* budget stands: the first attempt
      got the whole ceiling and a later one gets only what it left unspent. A "health never ready"
      attempt leaves nothing, so no retry follows it — a second full wait against an unchanged device
      would double the worst case for no new information.
    - `None` reports no recovery at all (`_no_recovery`, for a real device or a caller that opts out),
      which keeps the shared budget and adds no note.

    Worst-case wall time is therefore `attempts` ceilings plus whatever `recover` spends repairing the
    device, which `_recovery_timeout()` bounds — plus, when a repair earns a fresh budget, the same
    unbounded re-prep the first cold bring-up already pays before this loop even starts. A lane's
    startup-timeout headroom should be set against that worst case.
    """
    from bajutsu.drivers.xcuitest import XcuitestChannelError

    deadline = clock() + timeout
    diagnostics: list[str] = []
    for n in range(1, attempts + 1):
        remaining = deadline - clock()
        # First attempt always runs (with the full budget); a later one only if a fast failure or a
        # device repair left budget for it — an unchanged device that spent the whole ceiling gets no
        # wasteful second wait.
        if n > 1 and remaining <= 0:
            break
        spawned = spawn()
        try:
            failure = _await_cold_runner(
                spawned, timeout=max(0.0, remaining), poll=poll, sleep=sleep, clock=clock
            )
        except BaseException:
            # An unexpected failure while awaiting must not leak the just-spawned runner (the leak
            # BE-0290 prevents); discard it before propagating.
            spawned.discard()
            raise
        if failure is None:
            if n > 1:
                _logger.info(
                    "xcuitest runner unresponsive, respawned and recovered on attempt %d/%d (%s)",
                    n,
                    attempts,
                    "; ".join(diagnostics),
                )
            return spawned
        diagnostics.append(f"attempt {n}/{attempts}: {failure.detail}{spawned.log_tail()}")
        spawned.discard()
        if n == attempts:
            break  # no further attempt to prepare a device for
        # Recovery runs after the discard, so it acts on a device this run no longer holds a runner
        # on. It raises rather than returns when the device cannot be repaired at all (a host whose
        # Simulator runtimes are gone), which is a device fault, not a flaky spawn.
        try:
            recovery = recover(failure)
        except (simctl.DeviceError, OSError) as exc:
            # An unrepairable device still deserves every attempt's classified reason and captured
            # tail — that diagnostic is this function's whole point (unit 2) — so it is folded in
            # rather than lost behind the bare DeviceError an operator would otherwise see alone.
            # OSError is caught alongside DeviceError because every rung's simctl call can raise it
            # (a fork that fails with EAGAIN/ENOMEM, an xcrun that has gone) and nothing on the
            # reboot/replace paths converts it — the same host degradation this ladder recovers from.
            diagnostics.append(f"recovery after attempt {n} failed: {exc}")
            raise simctl.DeviceError(
                "xcuitest runner did not come up:\n" + "\n".join(diagnostics)
            ) from exc
        if recovery is not None:
            diagnostics.append(f"recovery after attempt {n}: {recovery.note}")
            if recovery.fresh_budget is not None:
                deadline = clock() + recovery.fresh_budget
    raise XcuitestChannelError("xcuitest runner did not come up:\n" + "\n".join(diagnostics))


def _destination(device_type: str, udid: str) -> str:
    """Build the `xcodebuild -destination` for a Simulator or a real device (BE-0238).

    Both run the same `test-without-building`; only the platform differs — the Simulator's
    `iOS Simulator` vs a real device's `iOS`. `validated_udid` applies the shared device_id policy
    (chiefly: an id never leads with `-`, which xcodebuild would read as an option) to either id.
    """
    platform = "iOS" if device_type == "device" else "iOS Simulator"
    return f"platform={platform},id={simctl.validated_udid(udid)}"


class XcuitestEnvironment(_DeviceEnvironment):
    """The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator, or the
    same runner without simctl prep on a real device (BE-0019, real-device targeting BE-0238).

    The simctl sequence (erase / boot / install) is the standard iOS Simulator prep. The difference from
    the previous coordinate-CLI approach is how the app is driven: instead of launching the app via
    simctl and actuating over a coordinate CLI, we start an
    `xcodebuild test-without-building` subprocess that runs the BajutsuRunner XCTest target — the
    runner launches the app, starts an HTTP server on localhost, and Python drives it through the
    `XcuitestDriver` channel.
    """

    def __init__(
        self,
        actuator: str,
        udid: str,
        env_run: simctl.RunFn = simctl._real_run,
        *,
        respawn: bool = False,
    ) -> None:
        super().__init__(actuator, udid, env_run)
        # This *fresh* environment was built by the pool for a mid-run respawn — the pool already
        # cold-spawned this device once this run and had to build a new environment for it (the
        # failed-resume eviction path, `cached is None`). A cold start here gets the tighter respawn
        # readiness ceiling. The far more common crash path keeps the *same* environment (a mid-run
        # crash leaves the dead resident warm-cached, so the retry reuses this instance and respawns
        # cold in place) — `_cold_spawned_before` below catches that. See `_respawn_timeout` /
        # `_spawn_cold`. Both default False for a genuine first bring-up, which keeps the cold ceiling.
        self._respawn = respawn
        # True once this instance has cold-spawned at least once: a *second* `_spawn_cold` on the same
        # environment is an in-place respawn (its warm resident died, so `start` discards it and
        # re-spawns cold), so it too takes the respawn ceiling — the ceiling must not depend only on
        # `_respawn`, which a reused instance built at first bring-up never has set.
        self._cold_spawned_before = False
        self._runner_proc: subprocess.Popen[bytes] | None = None
        self._runner_port: int = 0
        self._patched_runner: Path | None = None
        # Where the current runner's captured output went; a mid-run-crash warning and a startup
        # failure both point at it (`_runner_log_hint`). Capture is on by default (BE-0319 unit 1).
        self._runner_log: Path | None = None
        # True when `_runner_log` is a default (env-unset) capture teardown should prune; False when
        # it is an explicit `BAJUTSU_XCUITEST_RUNNER_LOG` directory the operator asked to keep.
        self._runner_log_ephemeral = False
        # The current spawn's run-ended probe over that capture, shared by the cold-spawn gate and the
        # mid-run liveness predicate (BE-0354); the neutral probe until a spawn wires a real one.
        self._run_ended: Callable[[], str | None] = _never_ended
        # BE-0291: True once a Simulator `start` has left a runner the pool should keep warm across
        # leases. A real-device start (BE-0238) never sets it — warm reuse targets only the Simulator
        # runner's cold startup — so the pool tears such an environment down per lease, unchanged.
        self._reusable = False
        # The locale this device's SpringBoard is currently pinned to (BE-0320), or None when nothing
        # has pinned it yet. Set only by a cold `_prepare_simulator` that verified the write landed,
        # and compared before a warm reuse — a scenario running under a different locale must not be
        # served by a runner whose SpringBoard is still rendering the previous one.
        self._pinned_locale: str | None = None
        # How many times the current runner has been reused warm (BE-0287): reset on a cold spawn,
        # incremented on each warm resume, and capped by `_max_warm_reuses()` so the runner is
        # respawned cold before it accumulates enough app.launch() cycles to crash mid-scenario.
        self._warm_reuses = 0
        # The app the runner launches, remembered on the Simulator path so a discard can terminate it
        # (`_terminate_app_under_test`). None until the first spawn, and on a real device, where
        # simctl does not apply.
        self._bundle_id: str | None = None
        # The XCTRunner apps of the .xctestrun this environment spawned, read out of its plist so a
        # discard can terminate the runner app itself (`_terminate_runner_app`). Empty until the
        # first spawn, and on a real device, where simctl does not apply.
        self._runner_bundle_ids: tuple[str, ...] = ()
        # This device's `deviceTypeIdentifier` and runtime identifier, captured while it is healthy
        # so a replacement can be cloned from it after it vanishes (`_replace_vanished_device`).
        self._device_type_id: str | None = None
        self._device_runtime_id: str | None = None
        # The udid this environment started on, once a vanished device forced a replacement — the flag
        # `replaced_device()` reports the swap by, so the pool can re-key what it holds per device.
        self._replaced_from: str | None = None
        # Set by `request_device_replacement` when the run pipeline escalates a crash retry above the
        # forced erase (BE-0354); consumed by the next `start`, which swaps the device before it preps.
        self._replacement_requested = False

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        ios = require_ios(eff)
        xcfg = ios.xcuitest
        device_type = effective_device_type(xcfg)
        # Read once and cleared here rather than where it is honored, so no `start` can leave a stale
        # escalation behind for a later lease — including the real-device route below, which returns
        # before the rung and has no simctl to mint a device through anyway.
        replace_device = self._replacement_requested
        self._replacement_requested = False

        if device_type == "device":
            # A real device is not managed through simctl: it is already powered on, its build is
            # installed out of band, and `simctl privacy` cannot reach it. The simctl-only
            # preconditions it cannot honour fail loudly here (real-device install / permissions
            # are BE-0238 Unit 2/3) rather than silently no-op'ing — determinism first.
            if pre.erase:
                raise simctl.DeviceError(
                    "erase is a simctl operation and does not apply to a real device "
                    "(xcuitest.deviceType: device)"
                )
            if ios.app_path:
                raise simctl.DeviceError(
                    "installing appPath through simctl does not apply to a real device "
                    "(xcuitest.deviceType: device); install the app and its device-build test "
                    "runner out of band"
                )
            if permissions:
                raise simctl.DeviceError(
                    "permission grants use simctl and do not apply to a real device "
                    "(xcuitest.deviceType: device)"
                )
            return self._spawn_cold(eff, pre, device_type, extra_env, permissions)

        # A pending escalation (BE-0354) is served before anything else touches the device: the run
        # pipeline asked for a replacement because an erase was already tried on this one and did not
        # clear the degradation, so preparing or reusing the degraded device first would only spend
        # the remedy the escalation exists to skip. The swap leaves the environment on a device that
        # has never run anything, which is a cold spawn by construction — no warm runner to reuse.
        if replace_device:
            self._discard_runner()
            self._replace_degraded_device(eff)
            # The erase is dropped *here*, where the swap actually happened, rather than by the
            # caller that asked for it: a device this method just created has nothing to erase, and
            # honoring the precondition would pay a second shutdown-and-boot cycle on it for no state
            # change. Deciding it caller-side would mean predicting this branch — and a request that
            # never reached this instance (a lease whose environment the pool had already evicted)
            # would then leave the retry with neither remedy.
            return self._spawn_cold(
                eff, pre.model_copy(update={"erase": False}), device_type, extra_env, permissions
            )

        # Simulator: reuse a healthy warm runner across leases (BE-0291). `erase` shuts the Simulator
        # down (killing the runner), so a scenario that erases forces a cold respawn; a wedged runner
        # is a cache miss too (Unit 4), costing one extra cold start rather than the run. A scenario
        # under a different locale than the one this device's SpringBoard is pinned to forces a cold
        # respawn for the same reason (BE-0320): only a cold spawn re-pins and reboots, so reusing
        # the warm runner would run the scenario against the previous scenario's language. The reuse
        # budget (`_max_warm_reuses`) forces a cold respawn *before* the runner accumulates enough
        # app.launch() cycles to crash mid-scenario (BE-0287) — a proactive refresh, checked before the
        # health probe so a spent runner skips straight to a cold spawn.
        if (
            not pre.erase
            and self._pinned_locale == pre.resolved_locale(eff.locale)
            and self._warm_reuses < _max_warm_reuses()
            and (driver := self._healthy_resident_driver()) is not None
        ):
            return self._resume_warm(eff, pre, extra_env, permissions, driver)
        self._discard_runner()  # drop any dead / lingering / reuse-spent runner before a fresh spawn
        return self._spawn_cold(eff, pre, device_type, extra_env, permissions)

    def _spawn_cold(
        self,
        eff: Effective,
        pre: Preconditions,
        device_type: str,
        extra_env: Mapping[str, str] | None,
        permissions: Mapping[str, str] | None,
    ) -> base.Driver:
        """Bring the runner up from cold: simctl prep (Simulator only), then spawn `xcodebuild`.

        The simctl device prep runs once on the healthy path; only the `xcodebuild` spawn is retried
        (BE-0319 unit 4). `_spawn_cold_with_retry` awaits readiness with a liveness check (unit 3),
        retries a one-off cold-start blip once, discards every failed attempt (no leaked subprocess —
        the leak BE-0290 prevents), and on a repeatable failure fails loudly with each attempt's
        captured tail. Between attempts on a Simulator it also repairs the device
        (`_recover_between_attempts`), which is the one path that runs the prep a second time.
        """
        ios = require_ios(eff)
        self._bundle_id = ios.bundle_id if device_type != "device" else None
        if device_type != "device":
            self._prepare_simulator(eff, pre, permissions, cold=True)

        # The runner launches the app via XCUIApplication.launch(). Preconditions are forwarded
        # through env vars: the runner reads BAJUTSU_LAUNCH_ENV_* and sets them on
        # launchEnvironment, BAJUTSU_LAUNCH_ARGS as launchArguments, and opens BAJUTSU_DEEPLINK.
        launch_env, launch_args = self._launch_params(eff, pre, extra_env)
        runner_path = _resolve_runner(ios.xcuitest, device_type)
        forwarded_base = {
            # One generic runner drives whatever app the run targets, so it launches this
            # bundle id via XCUIApplication(bundleIdentifier:) rather than its own target app.
            "BAJUTSU_BUNDLE_ID": ios.bundle_id,
            **{f"BAJUTSU_LAUNCH_ENV_{k}": v for k, v in launch_env.items()},
            "BAJUTSU_LAUNCH_ARGS": json.dumps(launch_args),
        }
        if pre.deeplink is not None:
            forwarded_base["BAJUTSU_DEEPLINK"] = pre.deeplink

        def spawn() -> _Spawned:
            return self._spawn_runner(runner_path, forwarded_base, device_type)

        # A cold `xcodebuild test-without-building` spins up the XCTest host and launches the app
        # before the runner's server answers /health; on a loaded CI runner that first start well
        # exceeds the 10s default, so give it generous headroom (a warm start still returns at once).
        # A respawn (the Simulator already booted, the app installed) gets the tighter respawn ceiling
        # when the lane sets one, so a dead runner surfaces fast instead of paying the full cold budget.
        # A respawn is either a fresh env the pool built for one (`_respawn`) or *this* env cold-spawning
        # a second time in place — its warm resident died, `start` discarded it, and we are re-spawning
        # (`_cold_spawned_before`), the common mid-run-crash path a reused instance takes. But `erase`
        # shuts the Simulator down (this method's `_prepare_simulator` then reboots and reinstalls), so a
        # post-erase spawn is a genuine first-boot cold start — not a respawn onto a live Simulator — and
        # must keep the full cold ceiling even though this instance has cold-spawned before.
        is_respawn = (self._respawn or self._cold_spawned_before) and not pre.erase
        respawn_ceiling = _respawn_timeout() if is_respawn else None
        timeout = respawn_ceiling if respawn_ceiling is not None else _runner_startup_timeout()

        # A real device has no simctl to recover through — it is powered on out of band — so it keeps
        # the plain retry. Only the Simulator gets the recovery ladder.
        def recover(failure: _AttemptFailure) -> _Recovery | None:
            return self._recover_between_attempts(failure, eff, pre, permissions)

        spawned = _spawn_cold_with_retry(
            spawn, timeout=timeout, recover=_no_recovery if device_type == "device" else recover
        )
        # A later cold spawn on this same instance is an in-place respawn, so tighten its ceiling too.
        self._cold_spawned_before = True
        # Only the Simulator runner is kept warm; a real-device runner is torn down per lease.
        self._reusable = device_type != "device"
        self._warm_reuses = 0  # a fresh XCTest session: the app.launch()-cycle count starts over
        return spawned.driver

    def _recover_between_attempts(
        self,
        failure: _AttemptFailure,
        eff: Effective,
        pre: Preconditions,
        permissions: Mapping[str, str] | None,
    ) -> _Recovery:
        """Repair the Simulator a failed cold attempt leaves behind, so the retry spawns onto a live device.

        Times the repair proper and checks it against `_recovery_timeout()` once the rung returns — a
        bound on a *slow* recovery, not a hard ceiling: every rung's `simctl` call goes through
        `self._run`, which carries no subprocess-level timeout, so a rung whose call itself wedges (the
        exact CoreSimulator degradation this ladder exists to recover from) is never interrupted, and
        only the run's own outer timeout catches it. Covers every rung that does return promptly,
        including the two that deliberately change nothing: the probe that opens the ladder is itself a
        subprocess, so a host slow enough to blow the bound merely answering `simctl list` is a host the
        run must give up on, whatever the rung then decided.

        Deliberately excludes the re-prep (`_finish_repair`) that a reboot or replacement earns: that
        prep is the same erase/locale-pin/install work the first bring-up already runs with no bound
        of its own, so a device that demonstrably came back should not fail the run over how long its
        reinstall took.

        Raises:
            DeviceError: if the device cannot be repaired — no replacement can be created, or the repair
                overran `_recovery_timeout()`. A device that will not come back is a device fault, not a
                flaky spawn, so it fails the run rather than funding another doomed attempt.
        """
        started = time.monotonic()
        recovery = self._recovery_rung(failure, eff)
        self._check_recovery_budget(started, recovery.note)
        if recovery.fresh_budget is not None:
            # Only a reboot or a replacement earns a fresh budget, and both leave the device
            # freshly booted but not yet re-prepared — the state the caller's own cold spawn
            # is about to run `xcodebuild` against.
            self._finish_repair(eff, pre, permissions)
        return recovery

    def _recovery_rung(
        self,
        failure: _AttemptFailure,
        eff: Effective,
    ) -> _Recovery:
        """Probe the device and run the one rung its state and this failure call for.

        The rung is chosen by what the attempt failed *on*, because the failures differ in what they
        say about the device. A device simctl no longer lists has to be replaced outright; an
        `xcodebuild` that exited on its own says nothing about the device, so the app it may have left
        running is all there is to clean up; an app-launch timeout or a wait that reached its ceiling
        says the device stopped honouring automation, which only a reboot clears.

        Both repairs that actually change the device earn the same fresh ceiling. A **reboot** ends with
        the device booted (`bootstatus` waited for it) and `_finish_repair` about to reinstall onto it; a
        **replacement** is a device that has never run anything. Either way `_finish_repair`'s reinstall
        is about to run `xcodebuild test-without-building` against a device in a genuine first-boot
        state — fresh CoreSimulator caches, a restarted SpringBoard, no prior XCTest host this boot — the
        same state `_spawn_cold` already gives the full cold ceiling on its own erase path regardless of
        this instance's respawn history (see the `is_respawn` comment above). Handing a rebooted respawn
        only the tighter respawn ceiling it started on would size that first bring-up for a warm reuse it
        is not. A reboot that could not even confirm the device left `Booted` earns neither: nothing
        changed, so it carries a note and no fresh budget like the two do-nothing rungs below.

        Returns the note and fresh readiness ceiling `_spawn_cold_with_retry` folds into its diagnostics
        and budget; a rung that changed nothing about the device carries a note and no fresh budget.
        """
        probe = simctl.device_available(self._udid, self._run)
        if probe is False:
            note = self._replace_vanished_device(eff)
            return _Recovery(note, fresh_budget=_runner_startup_timeout())
        if probe is None:
            # The listing itself failed, so nothing is known about the device. Repairing on a guess
            # could reboot a healthy device — or replace one that never vanished — so this rung
            # deliberately does nothing beyond what the discard already did.
            return _Recovery("could not probe the device; left it as it is")
        if failure.kind == "process-exit":
            # `xcodebuild` gave up by itself and fast, which is the transient blip BE-0319's retry was
            # written for. The discard has already terminated the app, so the device needs nothing.
            return _Recovery("xcodebuild exited on its own; device left booted")
        return self._reboot_device()

    def _check_recovery_budget(self, started: float, note: str) -> None:
        """Fail the run when a recovery rung overran its wall bound.

        Checked after the rung rather than inside it: the simctl steps are blocking calls this cannot
        preempt, so the bound catches a device that took absurdly long to come back rather than
        cutting a boot short. A rung that overran has spent the budget the retry would need anyway.

        Raises:
            DeviceError: if the rung took longer than `_recovery_timeout()`.
        """
        spent = time.monotonic() - started
        if spent > _recovery_timeout():
            raise simctl.DeviceError(
                f"Simulator recovery exceeded {_recovery_timeout()}s (spent {spent:.1f}s): {note}"
            )

    def _reboot_device(self) -> _Recovery:
        """Shut the Simulator down and boot it back up. `_finish_repair` re-establishes scenario state.

        `Env.shutdown()` suppresses its own failure — right for the benign "already shutting down"
        case it was written for, but a CoreSimulator wedged enough to stop honouring automation is
        exactly where `simctl shutdown` itself fails. Left unchecked, that failure is invisible: `boot`
        no-ops on a device that never left `Booted`, `bootstatus -b` sees it already booted and returns
        at once, and the retry would spawn onto the same still-wedged device with a fresh ceiling
        instead of the exhausted shared one — the pre-recovery stall, plus one extra ceiling of wall
        time. Reading the device's booted state back after `shutdown` is what tells a real reboot from
        a no-op, the same read-back `_pin_system_locale` already does for a `defaults write` that can
        exit 0 without surviving the shutdown. The read-back is itself three-valued, like
        `device_available`: the same wedged host that makes `shutdown` no-op can also make `simctl
        list devices booted` fail outright, and an unreadable listing confirms a reboot no more than
        a listing that still shows the device up does.
        """
        e = simctl.Env(self._udid, run=self._run)
        try:
            e.shutdown()
            if simctl.device_booted(self._udid, self._run) is not False:
                _logger.warning(
                    "Simulator %s did not shut down; the reboot rung had no effect", self._udid
                )
                return _Recovery(f"{self._udid} would not shut down; left as it is")
            e.boot()
            self._run(simctl.bootstatus_cmd(self._udid), None)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc
        _logger.warning("rebooted Simulator %s after a failed cold runner spawn", self._udid)
        return _Recovery(f"rebooted {self._udid}", fresh_budget=_runner_startup_timeout())

    def _finish_repair(
        self, eff: Effective, pre: Preconditions, permissions: Mapping[str, str] | None
    ) -> None:
        """Re-prepare the device a reboot or replacement just brought back, outside the recovery bound.

        The erase / locale-pin / install cycle this runs is exactly what the first cold bring-up
        already pays with no timeout of its own (`_spawn_cold`), so holding the repaired device to
        the same policy here — rather than folding it into `_check_recovery_budget` — keeps a device
        that genuinely recovered from failing the run over a slow but successful reinstall.
        """
        try:
            self._prepare_simulator(eff, pre, permissions, cold=True)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc

    def request_device_replacement(self) -> None:
        """Escalate the next `start` to a replacement device (BE-0354).

        The run pipeline calls this when a crash retry that already forced an erase crashed again, or
        when the attempt's video-start confirmation stalled — both say the degradation lives in the
        device's services rather than its data, which an erase resets and a replacement does not
        inherit. Recorded rather than acted on: the swap belongs to the next bring-up, where
        `replaced_device` then reports it and the pool re-keys everything it holds by udid.
        """
        self._replacement_requested = True

    def _replace_degraded_device(self, eff: Effective) -> None:
        """Move this environment onto a fresh Simulator, quarantining the degraded one (BE-0354).

        The rung above the crash retry's forced erase. An erase resets the device's data, so it
        recovers the app-data corruption class; the failure this serves is the other one — a Simulator
        whose capture services have wedged under a runner whose HTTP server still answers, which the
        erase was measured not to clear. A device that has never run anything cannot inherit that
        state, and the machinery to mint one already exists for the vanished-device rung.

        The degraded device is shut down and, because the pool follows `replaced_device` onto the
        replacement, never freed back to the queue — the same quarantine a vanished device gets today.
        The shutdown is best effort (`Env.shutdown` suppresses its own failure): a CoreSimulator wedged
        enough to refuse it is exactly why the run is leaving this device, so failing here would only
        replace one loud failure with a less useful one. It runs only once a replacement is known to be
        creatable, so a host that cannot mint one leaves the degraded device up for the caller's
        fallback rather than turned off on the way to a loud failure.

        Raises:
            DeviceError: as `_replacement_target` does, before anything about the device changes.
        """
        old = self._udid
        # Trigger-neutral wording: the escalation also fires on a stalled video start, from the
        # *first* crash, where no erase was ever forced — and this clause reaches the operator on the
        # path where no replacement could be made, so it must not claim one was.
        device_type = self._replacement_target(eff, why="needs replacing after a crash")
        simctl.Env(old, run=self._run).shutdown()
        note = self._create_replacement(eff, device_type)
        # The spawn that follows is a genuine first bring-up — a device just created and booted, with
        # no app installed and no XCTest host this boot — so it earns the full cold readiness ceiling,
        # not the tighter respawn one this environment's history would otherwise select. Exactly the
        # reasoning `_spawn_cold`'s own erase path already applies.
        self._respawn = self._cold_spawned_before = False
        _logger.warning(
            # Which signal selected this rung is the pipeline's to log; saying "after a forced erase"
            # here would misreport the stall-triggered path, which escalates from the first crash.
            "Simulator %s could not be recovered in place; shut it down and %s",
            old,
            note,
        )

    def _replace_vanished_device(self, eff: Effective) -> str:
        """Create a Simulator to take the place of one simctl no longer lists. `_finish_repair` preps it.

        Observed on CI as an `xcodebuild` exiting with "Unable to find a device matching the provided
        destination specifier" while the host's whole iOS device set had gone. Retrying onto a device
        that no longer exists cannot work, so the run continues on a replacement — and `bootstatus`
        runs here so a fresh device's first boot is paid before `_finish_repair`'s prep rather than
        inside the next attempt's readiness ceiling.

        Raises:
            DeviceError: as `_replacement_target` does — no device type to clone, or no `appPath`.
        """
        old = self._udid
        note = self._create_replacement(eff, self._replacement_target(eff, why="is gone"))
        _logger.warning("Simulator %s vanished from CoreSimulator; %s", old, note)
        return f"{old} vanished; {note}"

    def _replacement_target(self, eff: Effective, *, why: str) -> str:
        """The device type a replacement would be cloned from, or a loud failure. Changes nothing.

        Held apart from `_create_replacement` so both rungs can find out whether a replacement is
        possible *before* they touch the device they are leaving: the crash-retry rung shuts the
        degraded device down, and turning it off on the way to a failure would leave the caller's
        fallback worse off than no escalation at all. `why` names the caller's case, since an operator
        reading either message needs to know which rung ran.

        Raises:
            DeviceError: if no replacement can be created — chiefly a host that lost its iOS runtimes
                along with the device, where there is nothing left to run on — or if the target
                configures no `appPath`, since a blank replacement would have no app to install.
        """
        old = self._udid
        # A replacement is a blank device, so without an `appPath` to install onto it the retry has
        # nothing to launch: say so here rather than spending the create, the boot and a full cold
        # ceiling proving it.
        if require_ios(eff).app_path is None:
            raise simctl.DeviceError(
                f"Simulator {old} {why} and this target configures no appPath, so a replacement "
                "device would have no app to launch; set appPath so the recovery can install it, "
                "or bring a fresh Simulator up with the app installed and re-run"
            )
        device_type = self._replacement_device_type(eff)
        if device_type is None:
            raise simctl.DeviceError(
                f"Simulator {old} {why} and no device type matching {eff.device} is available to "
                "replace it; the host's Simulator runtimes may be gone, or the configured device "
                "name doesn't exactly match a simctl device type"
            )
        return device_type

    def _create_replacement(self, eff: Effective, device_type: str) -> str:
        """Create, boot, and adopt a fresh Simulator of `device_type`; the diagnostic note.

        Shared by both rungs that replace a device — the vanished-device rung of a failed cold spawn
        (BE-0344) and the crash retry's escalation above the forced erase (BE-0354) — so the naming,
        the runtime cloning, and the `replaced_device` bookkeeping the pool re-keys on cannot drift
        between them. `bootstatus` runs here so a fresh device's first boot is paid before the
        caller's prep rather than inside a readiness ceiling.

        The replacement **outlives the run**: nothing here or in the pool's teardown deletes it. That is
        deliberate on two counts. It is a healthy device a later run can simply lease, where deleting it
        would make the next run pay another creation on a host that has already shown it loses devices;
        and it is the evidence that this happened at all, which a run that deleted its own replacement
        would leave only in a log line. The cost is one new `bajutsu-recovered-*` device per *run* that
        replaces a device — not one per loss, since nothing here or later adopts an
        existing replacement: the `booted` alias self-heals (a replacement is left booted, so it resolves
        next time), but a config or `--udid` pinned to a permanently-vanished device mints a fresh,
        identically-named replacement on every run instead of converging on the one already created.
        That residue is also why the crash-retry rung is scoped to an unpinned run (see
        `bajutsu/backends.py`'s `device_replacement_supported`).
        Cleared by `xcrun simctl delete unavailable`, by deleting the `bajutsu-recovered-*` devices —
        which the name below makes greppable — or by re-pointing the pinned config at the replacement.

        Raises:
            DeviceError: if the fresh device's first boot never completes.
        """
        old = self._udid
        # The model comes first because two consumers read a device's name as its human model: the
        # report's device row, and `serve`'s capability inventory, which takes the `iphone` / `ipad`
        # class token out of it by substring. The `bajutsu-recovered-<udid>` suffix is what lets an
        # operator reading `simctl list` afterwards tell which recovery minted which device.
        name = f"{simctl.device_type_label(device_type)} (bajutsu-recovered-{old})"
        # Pinning the replaced device's own runtime keeps the replacement on the same iOS version a
        # scenario was written against; `create_device` retries unpinned if that runtime is itself
        # gone, so this only trades away version fidelity in the case it has to.
        requested_runtime = self._device_runtime_id
        replacement = simctl.create_device(
            device_type, self._run, name=name, runtime=requested_runtime
        )
        self._udid = replacement
        self._replaced_from = old
        self._pinned_locale = None  # a fresh device: nothing has pinned its SpringBoard yet
        # Cleared, not set: `create_device` falls back to an unpinned create when the pinned runtime
        # is gone, so what it got is not necessarily what was asked for. `_finish_repair`'s prep
        # re-reads both from the replacement itself.
        self._device_type_id = self._device_runtime_id = None
        try:
            self._run(simctl.bootstatus_cmd(replacement), None)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc
        return (
            f"created replacement {replacement} "
            f"({device_type}, requested runtime {requested_runtime or 'any'})"
        )

    def _replacement_device_type(self, eff: Effective) -> str | None:
        """The device type a replacement is created from, or None when no matching type is available.

        Prefers the vanished device's own type, so the replacement is the device the run was written
        against; falls back to the configured model, then — only for an iPhone target — to whichever
        iPhone this host's Xcode ships, since the config may name a model a later Xcode dropped and
        any iPhone beats failing the run. The fallback is scoped to an iPhone target because it is not
        scoped to a device *class*: `eff.device` matches simctl's device-type name exactly, and an
        iPad's name carries parentheses (`iPad Pro (12.9-inch) (6th generation)`), so a near-miss is
        ordinary rather than exotic — substituting an iPhone for a missed iPad would finish the run on
        a layout the scenario was never written against, silently.
        """
        if self._device_type_id is not None:
            return self._device_type_id
        configured = simctl.device_type_identifier(eff.device, self._run)
        if configured is not None:
            return configured
        if "iphone" not in eff.device.lower():
            return None
        return simctl.newest_iphone_device_type(self._run)

    def _spawn_runner(
        self, runner_path: Path, forwarded_base: Mapping[str, str], device_type: str
    ) -> _Spawned:
        """Spawn one `xcodebuild test-without-building` runner and hand back its liveness handles.

        Allocates a fresh port per attempt (so successive respawns leave separate capture files),
        patches the .xctestrun with the forwarded env, captures the runner's output, and builds the
        channel driver. Returns a `_Spawned` reading from this environment's just-set state — the
        surviving attempt's state is the environment's, which warm reuse and teardown then own.
        """
        self._runner_port = _allocate_port()
        forwarded = {"BAJUTSU_RUNNER_PORT": str(self._runner_port), **forwarded_base}
        # `xcodebuild` does not pass its own environment through to the test-runner process
        # inside the Simulator, so the runner reads these from the .xctestrun's per-target
        # TestingEnvironmentVariables instead. Patch a private copy and run that.
        self._patched_runner = _patch_xctestrun_env(runner_path, forwarded)
        # Read the runner app's own bundle id off the same plist while it is resolved, so a discard
        # can terminate the guest process `xcodebuild`'s process group cannot reach. Scoped to the
        # Simulator like `_bundle_id`: a real device has no simctl to terminate through.
        self._runner_bundle_ids = (
            _runner_host_bundle_ids(runner_path) if device_type != "device" else ()
        )
        runner_out = self._open_runner_output()
        # One probe per spawn over this attempt's capture (the port keys the file), so a retry starts
        # from an empty offset on its own log. Built before the driver below, which reads it through
        # `_runner_alive`, and shared with the cold gate — a second instance would race this one for
        # the marker (BE-0354).
        self._run_ended = _run_ended_probe(self._runner_log)
        try:
            proc = subprocess.Popen(
                [  # noqa: S607 — xcodebuild resolved on PATH; requires Xcode
                    "xcodebuild",
                    "test-without-building",
                    "-xctestrun",
                    str(self._patched_runner),
                    "-destination",
                    # Simulator vs real device (BE-0238); `_destination` validates the udid inline
                    # before it lands on the argv, the same defense-in-depth simctl applies.
                    _destination(device_type, self._udid),
                ],
                env={**os.environ, **forwarded},
                stdout=runner_out,
                # Fold stderr into the same sink so a crash's cause is captured in order.
                stderr=subprocess.STDOUT,
                # Own process group, so teardown reaches the XCTest-host plumbing `xcodebuild`
                # spawned rather than only `xcodebuild` itself — a signal that stops at the parent
                # leaves children holding the device's automation session (`_discard_runner`). The
                # trade: this also takes the runner out of the CLI's foreground process group, so a
                # terminal Ctrl-C no longer reaches `xcodebuild` directly — cleanup then depends on
                # Python's own exception handling (`_spawn_cold_with_retry`'s and `lease()`'s
                # `except BaseException`, `run.py`'s `finally: shutdown()`), which still runs
                # `_discard_runner`'s sweep on a single interrupt or any other exception, covering the
                # common case. It does not cover a second interrupt landing mid-teardown, or a bare
                # SIGTERM/SIGKILL bypassing Python's cleanup entirely (`bajutsu/` installs no signal
                # handler) — either would orphan `xcodebuild` and its children in their own session, a
                # narrower version of the wedged-Simulator failure this unit exists to clear, left as a
                # known gap rather than closed with signal-handling machinery this module does not
                # otherwise need.
                start_new_session=True,
            )
        except OSError as exc:
            raise simctl.DeviceError(f"failed to start xcodebuild: {exc}") from exc
        finally:
            # `Popen` dups the fd into the child at spawn, so the parent's copy is no longer needed;
            # closing on the error path too means a failed spawn never leaks the log handle.
            runner_out.close()
        self._runner_proc = proc
        _logger.info("xcuitest runner output → %s", self._runner_log)

        driver = backends.make_driver(
            self._actuator,
            self._udid,
            runner_port=self._runner_port,
            runner_alive=self._runner_alive,
        )
        # `log_tail` / `discard` reach live environment state (`self._runner_log` / `self._runner_proc`);
        # they are valid only until the next `spawn()` overwrites it, which the strictly sequential
        # retry loop (spawn → await → log_tail → discard → next spawn) guarantees. A failed cold
        # attempt keeps its log (`keep_log`) and skips the mid-run-crash warning (its reason is
        # already in the raised error).
        return _Spawned(
            driver=driver,
            ready=cast(base.BackendLifecycle, driver).health_ready,
            poll=proc.poll,
            log_tail=self._runner_log_hint,
            discard=lambda: self._discard_runner(warn_on_crash=False, keep_log=True),
            run_ended=self._run_ended,
        )

    def _resume_warm(
        self,
        eff: Effective,
        pre: Preconditions,
        extra_env: Mapping[str, str] | None,
        permissions: Mapping[str, str] | None,
        driver: base.Driver,
    ) -> base.Driver:
        """Reuse the live runner: re-prep the device and relaunch the app, skipping the spawn (BE-0291).

        The same app-only restart `device_relauncher` does within a lease — terminate, relaunch with
        this scenario's env / args / locale, and open its deeplink — now applied across leases, so the
        runner (which drives whatever app is launched and holds no scenario state) is reused. The
        caller has already confirmed the runner is healthy (and `_reusable` is already set) and that the
        scenario does not erase, so the per-scenario device reset (`reinstall` / permissions) still runs
        before the app launches and a reused runner never weakens the isolation a cold lease gives
        (Unit 2). `driver` is the channel the health probe already built on the runner's port, returned
        as-is; the app-readiness wait is launch_driver's, the same as the cold path.
        """
        ios = require_ios(eff)
        self._prepare_simulator(eff, pre, permissions, cold=False)
        launch_env, launch_args = self._launch_params(eff, pre, extra_env)
        e = simctl.Env(self._udid, run=self._run)
        try:
            e.terminate(ios.bundle_id)
            e.launch(ios.bundle_id, launch_args, launch_env)
            if pre.deeplink is not None:
                e.openurl(pre.deeplink)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc
        self._warm_reuses += (
            1  # one more app.launch() cycle on this runner (toward the reuse budget)
        )
        return driver

    def _prepare_simulator(
        self,
        eff: Effective,
        pre: Preconditions,
        permissions: Mapping[str, str] | None,
        *,
        cold: bool,
    ) -> None:
        """The simctl device prep shared by the cold spawn and the warm resume.

        `cold` runs the full device reset (erase → boot); a warm resume skips it — the Simulator is
        already booted under the live runner, and `erase` would shut it down (so a warm resume never
        carries erase). Both reinstall the app and (re)apply permissions, so a reused runner starts
        each scenario from the same known state a cold lease does (BE-0291 Unit 2).
        """
        ios = require_ios(eff)
        e = simctl.Env(self._udid, run=self._run)
        try:
            if cold:
                if pre.erase:
                    e.shutdown()
                    e.erase()
                e.boot()
                # Remember what kind of device this is while it is still listed: a replacement is
                # cloned from this type and runtime, and by the time one is needed the device is gone.
                if self._device_type_id is None:
                    resolved = simctl.device_type_of(self._udid, self._run)
                    self._device_type_id, self._device_runtime_id = resolved or (None, None)
                self._pin_system_locale(e, pre.resolved_locale(eff.locale))
            if ios.app_path:
                if not Path(ios.app_path).exists():
                    raise simctl.DeviceError(
                        f"appPath not found: {ios.app_path} (build the app first)"
                    )
                if pre.reinstall == "clean" and not pre.erase:
                    e.uninstall(ios.bundle_id)
                e.install(ios.app_path)
            # Set permission state after install (a fresh install/erase resets TCC grants) but
            # before the app launches, so a prompt never blocks it (BE-0276).
            if permissions:
                e.apply_permissions(ios.bundle_id, permissions)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc

    def _pin_system_locale(self, e: simctl.Env, locale: str) -> None:
        """Force the Simulator's *system* language to `locale`, so SpringBoard renders it too (BE-0320).

        `locale` reaches the app through its own launch arguments (`simctl.locale_args`), but
        SpringBoard — which owns the permission prompts `handleSystemAlert` taps by label — is a
        separate process those arguments never reach. Writing the device's global domain needs a
        booted device (`simctl spawn`), and a running SpringBoard does not pick the value up live,
        so a write is followed by one more boot cycle before the caller installs and launches. The
        common case (already pinned) costs one read and no extra boot.

        The read-back after the reboot is what makes the pin a fact rather than a hope: a `defaults
        write` can exit 0 and still not survive the shutdown. Only a confirmed pin is remembered —
        `_pinned_locale` gates warm reuse, so recording an unconfirmed one would carry the doubt
        across every later lease instead of re-checking on the next cold spawn.

        Raises:
            DeviceError: if the reboot demonstrably left the device on another locale — the run would
                otherwise proceed against an alert language nothing predicts.
        """
        self._pinned_locale = None  # not pinned until the write below is confirmed
        if not e.pin_system_locale(locale):
            self._pinned_locale = locale  # the read already confirmed it; nothing was written
            return
        e.shutdown()
        e.boot()
        confirmed = e.system_locale_matches(locale)
        if confirmed is False:
            raise simctl.DeviceError(
                f"failed to pin the Simulator's system locale to {locale!r}; "
                "system-alert button labels would not be deterministic (BE-0320)"
            )
        if confirmed is None:
            # Nothing was observed to be wrong, so the run proceeds — but the pin is unconfirmed, so
            # it is not recorded: the next lease cold-spawns and re-checks rather than reusing a
            # runner on the strength of a write we could not read back.
            _logger.warning(
                "could not read the Simulator's global domain back after pinning it to %r; "
                "the run continues, but warm-runner reuse is disabled until a spawn confirms it "
                "(BE-0320)",
                locale,
            )
            return
        self._pinned_locale = locale

    def _launch_params(
        self, eff: Effective, pre: Preconditions, extra_env: Mapping[str, str] | None
    ) -> tuple[dict[str, str], list[str]]:
        """The launch env and args for this scenario (scenario locale overrides the config default)."""
        launch_env = {**eff.launch_env, **pre.launch_env, **(extra_env or {})}
        launch_args = [
            *eff.launch_args,
            *pre.launch_args,
            *simctl.locale_args(pre.resolved_locale(eff.locale)),
        ]
        return launch_env, launch_args

    def _runner_alive(self) -> bool:
        """Whether the runner can still answer on its port: its process runs *and* its test run has not ended.

        The crash-recovery layer reads this to split a recoverable blip from a dead runner: a runner
        that cannot come back will never answer `/health` again on its port, so recovery fails fast
        instead of polling it for the whole window (a runner merely unreachable but alive stays
        BE-0287's recoverable case). `poll()` is `None` while the process runs, an exit code once it
        has ended.

        The process handle alone is not enough (BE-0354). `xcodebuild` outlives its own test run by a
        long way: after a mid-run crash, XCTest restarts the in-Simulator host and re-runs zero tests,
        so the suite reports its result and the parent lives on — the blind spot BE-0305's
        fault-injection measurements recorded, where every recovery episode waits out its full window
        on a runner whose port will never bind again. The capture already names that state, and the
        cold-spawn gate has string-matched the same markers since BE-0319, so the probe reading it
        answers here too. It is the *same* probe instance the gate uses, latched: this predicate is
        re-asked once per recovery episode, while the probe advances a private offset and reports a
        marker only from the window that first contains it. A future Xcode that rewords the markers
        degrades this to the process-only check it replaces, never to a false "gone".
        """
        if self._runner_proc is None or self._runner_proc.poll() is not None:
            return False
        return self._run_ended() is None

    def _healthy_resident_driver(self) -> base.Driver | None:
        """The driver for the warm runner if it is up and answering `/health`, else None (BE-0291 Unit 4).

        A dead process, or a live one that fails a bounded `/health` probe, returns None: the caller
        respawns cold. The known failure is the runner crashing after repeated `app.launch()` cycles
        (docs/architecture.md), so this stays cheap and never waits the cold ceiling. The probed driver
        is returned (not rebuilt) so a warm resume reuses this same channel on the runner's port.
        """
        if self._runner_proc is None or self._runner_proc.poll() is not None:
            return None
        from bajutsu.drivers.xcuitest import XcuitestChannelError

        driver = backends.make_driver(
            self._actuator,
            self._udid,
            runner_port=self._runner_port,
            runner_alive=self._runner_alive,
        )
        try:
            cast(base.BackendLifecycle, driver).await_ready(timeout=_WARM_HEALTH_TIMEOUT)
        except XcuitestChannelError:
            return None  # wedged / unreachable — treat as a cache miss and respawn
        return driver

    def _open_runner_output(self) -> IO[bytes]:
        """Open the sink for the runner subprocess's combined output, capturing by default (BE-0319 unit 1).

        A cold spawn always captures, so the first CI flake is diagnosable without a human pre-arming
        `BAJUTSU_XCUITEST_RUNNER_LOG`; that variable now only *overrides the directory*. An env-unset
        capture goes to `_DEFAULT_RUNNER_LOG_DIR` and is marked ephemeral (teardown prunes it), while
        an explicit directory is kept. Sets `_runner_log` to the file it opened and returns it as the
        sink for `Popen`'s `stdout`.
        """
        log_dir_env = os.environ.get(_RUNNER_LOG_ENV)
        self._runner_log_ephemeral = not log_dir_env
        log_dir = Path(log_dir_env) if log_dir_env else _DEFAULT_RUNNER_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        # Port keys the file to this spawn (a fresh ephemeral port each cold start), so successive
        # respawns on one device leave separate logs rather than overwriting the crashed one.
        self._runner_log = log_dir / f"runner-{self._udid}-{self._runner_port}.log"
        return self._runner_log.open("wb")

    def _runner_log_hint(self) -> str:
        """A trailer for the crash warning and the startup-failure error: the captured log's path and tail."""
        if self._runner_log is None:
            return ""
        tail = ""
        try:
            # Keep only the last N lines without materializing the whole (high-volume) capture: deque
            # streams the file line by line and drops all but its tail.
            with self._runner_log.open(errors="replace") as fh:
                tail = "".join(deque(fh, maxlen=_RUNNER_LOG_TAIL_LINES)).rstrip("\n")
        except OSError:
            pass  # the log may not exist yet on a spawn that failed before writing
        return f"; see {self._runner_log}" + (f"\n{tail}" if tail else "")

    def _discard_runner(self, *, warn_on_crash: bool = True, keep_log: bool = False) -> None:
        """Terminate the runner process and remove its patched .xctestrun (kills the warm resident).

        `warn_on_crash` logs the mid-run-crash diagnostic when the process had already exited on its
        own — right for a resident runner that vanished mid-run (the known app.launch()-cycle crash),
        but wrong for a cold-spawn startup failure, whose reason is already folded into the raised
        error, so `_spawn_cold_with_retry` clears it. `keep_log` leaves the capture on disk for a
        failed cold attempt (the evidence a loud failure points at); teardown of a healthy runner
        prunes it (BE-0319). A mid-run crash keeps its capture too — `warn_on_crash`'s hint tells the
        operator to "see <path>", so pruning that same file in this call would point at evidence that
        no longer exists.

        Teardown reaches the whole process group, then the app under test, then the runner app,
        because what this discards is handed straight to another spawn on the same device: a runner
        whose children survived, an app left mid-launch, or an XCTRunner still holding the device's
        automation session is state the next attempt inherits. That includes a
        leader that already exited on its own: `start_new_session` (`_spawn_runner`) makes it its own
        process group leader, so an XCTest-host child can outlive it and keep holding the device's
        automation session even though `xcodebuild` is gone — exactly the state a following spawn
        attempt would then have to spawn onto. The sweep runs unconditionally, even though `poll()`
        here may not be the call that first reaped the leader (`_runner_alive`, the warm-resume health
        check, and `start()`'s own reuse probe all poll the same handle, and each is the dominant path
        into this branch) — while any XCTest-host child outlives the leader, POSIX keeps the leader's
        pid reserved as that child's process-group id, so it cannot have been recycled regardless of
        who reaped it or when; only once the group is *empty* — the case where the sweep has nothing
        left to reach anyway — is the pid free to be reused, the same narrow window the `else` branch
        below already accepts unconditionally via `_terminate_process_group`.
        """
        crashed = False
        if self._runner_proc is not None:
            exited = self._runner_proc.poll()
            if exited is not None:
                # The leader is already gone — it exited on its own, not at our request. For a
                # resident runner that is the known app.launch()-cycle crash (see `_MAX_WARM_REUSES`
                # above for what this repeatedly surfaced in CI); log it (with the captured output)
                # so a run that died on a `Connection refused` shows *why* the channel vanished.
                crashed = warn_on_crash
                if warn_on_crash:
                    _logger.warning(
                        "xcuitest runner exited on its own (code %s) — a mid-run crash%s",
                        exited,
                        self._runner_log_hint(),
                    )
                # No terminate() — the leader's pid is already reaped — but `start_new_session` made it
                # its own group leader, so that pid is still a valid pgid for as long as any child
                # survives it: sweep whatever XCTest-host children outlived the leader. A sweep of an
                # already-empty group raises ProcessLookupError, suppressed like every other discard step.
                with contextlib.suppress(OSError):
                    os.killpg(self._runner_proc.pid, signal.SIGKILL)
            else:
                _terminate_process_group(self._runner_proc)
            self._runner_proc = None
        # The capture this probe reads may be pruned below, and the next spawn wires its own.
        self._run_ended = _never_ended
        self._terminate_app_under_test()
        self._terminate_runner_app()
        self._release_log(keep=keep_log or crashed)  # after the hint above has read the tail
        if self._patched_runner is not None:
            self._patched_runner.unlink(missing_ok=True)
            self._patched_runner = None
        self._reusable = False

    def _terminate_app_under_test(self) -> None:
        """Best-effort `simctl terminate` of the app the runner launched (Simulator only).

        The Swift runner `_exit`s on a pre-serving failure rather than unwinding XCTest, so nothing
        else brings the app down: an app left mid-launch by the timeout that failed one attempt is
        exactly what the next attempt would call `launch()` on again. Every failure here is ignored —
        the common case is an app that is not running, and a discard must never fail.
        """
        if self._bundle_id is None:
            return
        with contextlib.suppress(subprocess.CalledProcessError, simctl.DeviceError, OSError):
            simctl.Env(self._udid, run=self._run).terminate(self._bundle_id)

    def _terminate_runner_app(self) -> None:
        """Best-effort `simctl terminate` of the XCTRunner app itself (Simulator only).

        The process-group sweep above cannot reach this one: `launchd_sim` starts the runner app
        inside the Simulator, so it is a guest process in no host process group, and it survives the
        `xcodebuild` that asked for it — still holding the automation session `testmanagerd` handed
        it. Every cold spawn that leaves one behind narrows what the next spawn can obtain, which is
        one way a device reaches the state where the runner never comes up at all. The ids come from
        the resolved `.xctestrun` (`_runner_host_bundle_ids`) rather than the bundled runner's known
        id, so an explicit `xcuitest.testRunner` is cleaned up just as readily. Failures are ignored
        for the same reason as the app under test: the common case is one that is not running.
        """
        for bundle_id in self._runner_bundle_ids:
            with contextlib.suppress(subprocess.CalledProcessError, simctl.DeviceError, OSError):
                simctl.Env(self._udid, run=self._run).terminate(bundle_id)

    def _release_log(self, *, keep: bool) -> None:
        """Drop the reference to the current capture, pruning a default (env-unset) one unless kept.

        A default capture exists only to diagnose a flake — its tail is folded into the crash warning
        / startup error before this runs — so a healthy run prunes it and leaves nothing behind. A
        failed cold attempt, and a mid-run crash (`_discard_runner`'s `keep_log or crashed`), both keep
        it, so the full log survives as evidence past the 20-line tail already shown; an explicit
        `BAJUTSU_XCUITEST_RUNNER_LOG` directory is always kept, since the operator asked for it
        (BE-0319 unit 1).

        A kept default capture is logged here at the moment it is kept: `_spawn_cold_with_retry`
        folds a failed attempt's path into the raised error only when *every* attempt fails: a retry
        that then succeeds raises nothing, so without this line that attempt's file becomes untracked
        the instant this environment's `_runner_log` moves on to the next attempt — orphaned in
        `_DEFAULT_RUNNER_LOG_DIR` with nothing pointing at it.
        """
        if self._runner_log is not None and self._runner_log_ephemeral:
            if keep:
                _logger.info(
                    "xcuitest runner: kept a failed attempt's capture → %s", self._runner_log
                )
            else:
                self._runner_log.unlink(missing_ok=True)
        self._runner_log = None

    def has_reusable_resident(self) -> bool:
        return self._reusable  # BE-0291: a Simulator start left a warm runner the pool should keep

    def replaced_device(self) -> str | None:
        # The udid this environment is on now, once a vanished device forced a replacement. Reported
        # unconditionally after the swap (not cleared per lease): the pool compares it against the
        # udid it leased, so a pool that has already adopted the replacement reads no further change.
        return self._udid if self._replaced_from is not None else None

    def end_lease(self, driver: base.Driver, eff: Effective) -> None:
        # Keep the warm runner alive for the next lease on this device; terminate only the app, the
        # same per-scenario cleanup a cold lease does (BE-0291). The pool tears the runner down later
        # (run-set end / actuator switch) via teardown.
        super().teardown(driver, eff)

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        self._discard_runner()
        super().teardown(driver, eff)


def effective_device_type(xcfg: XcuitestConfig | None) -> str:
    """The target's `xcuitest.deviceType`, defaulting to `"simulator"` when unconfigured.

    The one place this default lives, so `XcuitestEnvironment.start` and `runner_source`'s caller
    (BE-0292's doctor disclosure) read the same value instead of each re-deriving it.
    """
    return xcfg.device_type if xcfg is not None else "simulator"


_RunnerTier = Literal["misconfigured", "explicit", "device", "bundled"]


def _classify_runner(
    xcfg: XcuitestConfig | None, device_type: str
) -> tuple[_RunnerTier, str | None, str | None]:
    """Which runner-resolution tier applies: an explicit testRunner, else build, else the bundle.

    The one place the precedence lives, so `_resolve_runner` (which acts on the tier) and
    `runner_source` (which only discloses it, BE-0292) can't drift apart. Returns the tier plus
    `(test_runner, build)` when the tier is `"explicit"` (both `None` otherwise, since only that
    tier needs them).
    """
    test_runner = xcfg.test_runner if xcfg is not None else None
    build = xcfg.build if xcfg is not None else None
    if test_runner is None and build is not None:
        # `build` only ever refreshes the file at `testRunner` (see below); without that path there
        # is nowhere for its output to land, so this is a misconfiguration, not a request for the
        # bundled default.
        return "misconfigured", None, None
    if test_runner is not None:
        return "explicit", test_runner, build
    if device_type == "device":
        return "device", None, None
    return "bundled", None, None


def _resolve_runner(xcfg: XcuitestConfig | None, device_type: str) -> Path:
    """Resolve the `.xctestrun` to run: an explicit testRunner, else its build, else the bundle.

    Precedence keeps explicit config above the default. A configured `testRunner` is used, built on
    demand via `build` when the file is missing. With neither configured, a Simulator run falls back
    to the wheel-bundled generic runner (BE-0292), materialized into a writable cache; a real device
    instead fails loudly, since its runner must be signed (BE-0288) and is not bundled.
    """
    tier, test_runner, build = _classify_runner(xcfg, device_type)

    if tier == "misconfigured":
        # Fail loudly rather than silently ignoring the configured build.
        raise simctl.DeviceError("xcuitest.build requires xcuitest.testRunner (the path it builds)")

    if tier == "explicit":
        assert test_runner is not None  # guaranteed by _classify_runner's "explicit" tier
        runner_path = Path(test_runner)
        if not runner_path.exists() and build:
            try:
                subprocess.run(shlex.split(build), check=True)
            except (subprocess.CalledProcessError, OSError) as exc:
                raise simctl.DeviceError(f"xcuitest build command failed: {build}") from exc
        if not runner_path.exists():
            raise simctl.DeviceError(f"xcuitest testRunner not found: {test_runner}")
        return runner_path

    if tier == "device":
        raise simctl.DeviceError(
            "xcuitest.deviceType: device requires an explicit xcuitest.testRunner "
            "(a real-device runner must be signed and is not bundled; see BE-0288)"
        )
    products = bundled_products_dir()
    if products is None:
        raise simctl.DeviceError(
            "xcuitest backend requires xcuitest.testRunner in the target config "
            "(no bundled runner is present in this build)"
        )
    try:
        return materialize(products)
    except OSError as exc:
        raise simctl.DeviceError(
            f"failed to materialize the bundled xcuitest runner: {exc}"
        ) from exc


def runner_source(xcfg: XcuitestConfig | None, device_type: str) -> str:
    """Which runner-resolution tier a target would use, without acting on it (BE-0292).

    Shares `_resolve_runner`'s precedence via `_classify_runner` rather than re-deriving it, so
    `doctor` can disclose the source without running a configured `build` command or materializing
    the bundled runner into the cache.
    """
    tier, test_runner, build = _classify_runner(xcfg, device_type)

    if tier == "misconfigured":
        return "misconfigured: xcuitest.build requires xcuitest.testRunner"
    if tier == "explicit":
        assert test_runner is not None  # guaranteed by _classify_runner's "explicit" tier
        if Path(test_runner).exists():
            return f"testRunner: {test_runner}"
        if build:
            return f"testRunner: {test_runner} (missing, built on demand via: {build})"
        return f"testRunner: {test_runner} (missing, no build configured)"
    if tier == "device":
        return "none: xcuitest.deviceType: device requires an explicit testRunner"
    if bundled_products_dir() is None:
        return "none: no bundled runner in this build (set xcuitest.testRunner)"
    return "bundled (wheel-shipped Simulator runner)"


def _major(version: str) -> str:
    """The leading numeric component of a version like ``16.0`` or ``18.2`` — its major."""
    return version.split(".", 1)[0].strip()


def bundled_runner_toolchain_warning(
    build_info: Mapping[str, str] | None,
    host_xcode: str | None,
    host_sdk: str | None,
) -> str | None:
    """Warn when the host toolchain differs from the one the bundled runner was built against.

    The bundled runner is a compiled artifact tied to the Xcode and Simulator SDK it was built with
    (BE-0292); a host on a different major version can fail to launch it with an opaque `xcodebuild`
    error. Comparing majors keys the warning to that breaking case while staying quiet across the
    point releases that stay compatible. Returns a one-line message naming the `testRunner` / `build`
    overrides as the escape hatch, or `None` when there is nothing recorded, nothing on the host to
    compare, or the majors agree. Pure disclosure: no gate, no LLM (prime directive 1).
    """
    if not build_info:
        return None

    def _mismatch(label: str, built: str | None, host: str | None) -> str | None:
        if built and host and _major(built) != _major(host):
            return f"{label} {built} (bundled runner) vs {host} (host)"
        return None

    mismatches = [
        m
        for m in (
            _mismatch("Xcode", build_info.get("xcode"), host_xcode),
            _mismatch("iphonesimulator SDK", build_info.get("sdk"), host_sdk),
        )
        if m
    ]
    if not mismatches:
        return None
    return (
        "bundled runner toolchain mismatch: "
        + "; ".join(mismatches)
        + " — if it fails to launch, set xcuitest.testRunner or xcuitest.build to build a "
        "matching runner"
    )


def bundled_runner_toolchain_note(
    xcfg: XcuitestConfig | None,
    device_type: str,
    host_toolchain: Callable[[], tuple[str | None, str | None]],
) -> str | None:
    """A toolchain-mismatch note, but only when the target resolves to the bundled runner (BE-0292).

    Shares `_classify_runner`'s precedence so the note is confined to the bundled tier; an explicit
    `testRunner` or a device target (whose runner is not the bundled one) never warns. `host_toolchain`
    is a `() -> (xcode, sdk)` probe called lazily — only after the tier gate passes — so a target with
    an explicit runner pays no subprocess cost. Delegates the version comparison to
    `bundled_runner_toolchain_warning`.
    """
    tier, _, _ = _classify_runner(xcfg, device_type)
    if tier != "bundled":
        return None
    host_xcode, host_sdk = host_toolchain()
    return bundled_runner_toolchain_warning(bundled_runner_build_info(), host_xcode, host_sdk)


def _runner_host_bundle_ids(runner_path: Path) -> tuple[str, ...]:
    """The bundle ids of the XCTRunner apps a `.xctestrun`'s test targets are hosted by.

    Each target names its own runner app as `TestHostBundleIdentifier` (the built test bundle's id
    with `.xctrunner` appended), so reading it here covers the bundled runner and an explicit
    `xcuitest.testRunner` alike — neither path has to know the other's id. Duplicates are dropped,
    since several targets in one file can share a runner app. Best-effort: a plist that cannot be
    read yields nothing rather than failing the spawn that asked, the same posture as the terminate
    this feeds (in practice `_patch_xctestrun_env` has already parsed the same file by then).
    """
    try:
        with runner_path.open("rb") as f:
            plist = plistlib.load(f)
    except (OSError, ValueError):
        return ()
    ids: list[str] = []
    for key, target in plist.items():
        if key == "__xctestrun_metadata__" or not isinstance(target, dict):
            continue
        host_id = target.get("TestHostBundleIdentifier")
        if isinstance(host_id, str) and host_id and host_id not in ids:
            ids.append(host_id)
    return tuple(ids)


def _patch_xctestrun_env(runner_path: Path, forwarded: Mapping[str, str]) -> Path:
    """Write a copy of the .xctestrun with *forwarded* merged into each target's env.

    `xcodebuild` does not propagate its own environment into the Simulator test-runner
    process, so the runner reads `BAJUTSU_*` from `TestingEnvironmentVariables` (the runner
    process's env) instead. Returns the temp copy's path; the caller unlinks it on teardown.
    """
    with runner_path.open("rb") as f:
        plist = plistlib.load(f)
    for key, target in plist.items():
        if key == "__xctestrun_metadata__" or not isinstance(target, dict):
            continue
        env_vars = dict(target.get("TestingEnvironmentVariables") or {})
        env_vars.update(forwarded)
        target["TestingEnvironmentVariables"] = env_vars
    # `__TESTROOT__` in the plist resolves relative to the .xctestrun's own directory, so the
    # patched copy must sit beside the original (next to the built products) to still find them.
    fd, path = tempfile.mkstemp(suffix=".xctestrun", dir=str(runner_path.parent))
    with os.fdopen(fd, "wb") as f:
        plistlib.dump(plist, f)
    return Path(path)
