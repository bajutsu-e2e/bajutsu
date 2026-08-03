"""Drive the XCUITest crash-recovery path with a real runner fault, not a raised exception (BE-0305).

The channel carries two layers built for a runner that dies or hangs mid-run: BE-0207's `_with_retry`
smooths a sub-second blip, and BE-0287's `_with_crash_recovery` waits out a crash and re-issues an
idempotent call, or fails loudly on a write it must not re-send. Every existing check of them raises
`XcuitestRunnerCrashError` from a nested closure with health faked, which proves the branching but
leaves the classifier itself untested: whether the real socket failure of a frozen or killed resident
runner is still recognised as a crash at all, rather than surfacing as an unrelated timeout. The
on-device conformance suite never helps here — no job has ever interfered with a running runner.

Two real faults, in the order the module runs them:

1. **Frozen** — `SIGSTOP` the process listening on the runner's loopback port. The connection is
   still accepted (the kernel's backlog outlives the stopped process) and then never answered, so the
   transport hits its real socket timeout: the *hung connection* failure mode, which a refused
   connection would not reproduce. A background thread sends `SIGCONT` once the retry budget has
   certainly been spent, kept close to that budget rather than padded.
2. **Killed** — `SIGKILL` the runner and its `xcodebuild` host process. `runner_alive` then reports
   the process gone and recovery fails fast with the diagnosis naming that, instead of polling a dead
   port for the whole recovery window.

**The frozen runner is not expected back.** XCTest reclaims a runner it cannot reach — its own log
says "Restarting after unexpected exit, crash, or test timeout" — so a `SIGCONT` often resumes a
process XCTest has already condemned, and the re-issue that follows meets a closed connection and
then a refused one. Three successive shrinks of the freeze (~51.5 s, ~12.5 s, ~4.5 s) each lost the
runner the same way, which is what rules duration out as the variable: the watchdog reacts to the
runner being unreachable, not to how long for. So the frozen case asserts the two things the fault
does prove, both read off the recovery log — that the hang was classified as a mid-run crash, and
that recovery re-issued the call — and treats the re-issue's own outcome as XCTest's to decide.
Asserting the app comes back would be asserting that the watchdog lost a race.

The killed case therefore takes the port as it finds it: the frozen case ahead of it may already have
left the runner exited, which *is* this case's premise rather than a broken environment. It still
kills whatever holds the port, and still requires the `xcodebuild` host to be reaped, so the
`runner_alive` branch it drives is reached the same way either way. It is written last and must run
after the frozen one; source order plus `-n0` is what guarantees that, which is why both callers pass
`-n0` rather than inheriting the repo's default `-n auto`.

Running against a real, frozen socket costs real wall time — the transport budget plus a margin —
which no injected-clock unit test pays. Shrinking the budget (below) keeps that to seconds, but it
is still real time on a real device, and freezing a real runner is inherently less predictable than
faking one: that is why the lane lands as a non-gating signal first (`fault (xcuitest)` in
`ios-e2e.yml`), following BE-0282's precedent, and is promoted once stable.

Runs in the iOS E2E path, never in `make check`: the `ondevice` marker is deselected by the gate's
default, and a module-level skip drops it whenever `BAJUTSU_FAULT_UDID` is unset.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from bajutsu import simctl
from bajutsu.config import Effective, load_config, resolve
from bajutsu.drivers import base, xcuitest
from bajutsu.platform_lifecycle.environments.xcuitest import XcuitestEnvironment
from bajutsu.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

# The E2E workflow provisions a booted Simulator with the showcase app and signals it here; absent
# (any Linux box, the fast gate), skip the whole module. The `ondevice` marker also deselects it, so
# this is belt-and-braces — the suite never runs, or errors, off an on-device host.
_udid = os.environ.get("BAJUTSU_FAULT_UDID")
if not _udid:
    pytest.skip(
        "on-device XCUITest fault injection needs BAJUTSU_FAULT_UDID (a booted Simulator with the "
        "showcase app and the built runner) — it runs in the iOS E2E workflow, never the fast gate",
        allow_module_level=True,
    )
UDID: str = _udid

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-swiftui"

# The per-attempt socket timeout the transport runs at *while a fault is injected* (see
# `shrunk_retry_budget`, which scopes it to one test), in place of the shipped 15 s. Shipped, one
# `GET`'s worst-case retry budget is ~46.5 s, so the freeze would have to hold ~51.5 s for the retry
# loop to genuinely exhaust; at this value the budget is ~3 s and the freeze ~4.5 s, which is what
# takes this suite from minutes to seconds. Nothing about the mechanism under test changes — a real
# `SIGSTOP` still hangs a real socket, and the shorter timeout still forces three genuine socket
# timeouts before the classifier fires — only the wall clock it all happens on.
#
# This value is *not* what keeps the runner alive, though three successive shrinks (~51.5 s, ~12.5 s,
# ~4.5 s) were each tried in that hope and each lost it anyway. XCTest reclaims an unreachable runner
# regardless of how briefly it was unreachable (see the module docstring), so the frozen case no longer
# asserts the runner survives and there is nothing left here to tune for. Shrink it further only to
# make the suite quicker, never to chase a surviving runner.
_TEST_SOCKET_TIMEOUT_S = 0.5

# Added to the retry budget to get the freeze's total hold time (`_freeze_hold_s`). Only has to cover
# `threading.Timer` scheduling jitter — the budget itself already accounts for every attempt and
# backoff the retry loop spends — so it stays small rather than padded: the smaller the total freeze,
# the less time a resumed process spends exposed to whatever reclaims a long-stopped one.
_RELEASE_MARGIN_S = 1.5

_RECOVERY_LOGGER = "bajutsu.xcuitest.channel"


@pytest.fixture
def shrunk_retry_budget(driver: base.Driver) -> Iterator[None]:
    """Run the transport at `_TEST_SOCKET_TIMEOUT_S` per attempt, for one test's fault only.

    Function-scoped and depending on `driver` so the shrink starts *after* the runner is up. Held
    module-wide and autouse — which is how this landed — it also covered `launch_driver`'s readiness
    probe, and a cold XCTest runner answering its first `/elements` takes far longer than this
    timeout: both tests then errored in setup, the probe's reads timing out, being classified as a
    crash, and the recovery machinery taking the runner down before any fault was ever injected. The
    budget only ever needed to be small for the window the freeze has to outlast; everything else in
    the suite wants the shipped timeout.
    """
    mp = pytest.MonkeyPatch()
    mp.setattr(xcuitest, "_SOCKET_TIMEOUT_SECONDS", _TEST_SOCKET_TIMEOUT_S)
    try:
        yield
    finally:
        mp.undo()


def _freeze_hold_s() -> float:
    """How long to hold the freeze: the retry loop's worst case, plus `_RELEASE_MARGIN_S`.

    Read from the transport rather than restated, so re-tuning the retry loop re-tunes the fault with
    it, and computed at call time so it sees `shrunk_retry_budget`'s override. The margin stays
    small on purpose: this suite drives one request at a time, never several concurrently, so the
    kernel's accept backlog is never full and `connect()` is always served instantly — only the
    response read actually blocks for the socket timeout, which `_retry_budget_seconds` already
    counts. A wider margin buys nothing and costs the runner's survival.
    """
    budget = xcuitest._retry_budget_seconds("GET")
    hold = budget + _RELEASE_MARGIN_S
    # Released after the retry loop has certainly given up, and before recovery stops waiting:
    # outside either bound the suite would silently test something else (the retry absorbing the
    # freeze, or the never-recovered branch).
    assert budget < hold < budget + xcuitest._RECOVERY_TIMEOUT_SECONDS
    return hold


@pytest.fixture(scope="module")
def _eff() -> Effective:
    # Rebase against the config's own directory so the relative appPath / testRunner resolve from
    # here the way the CLI would (unconfined, like a local config, BE-0242).
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def environment() -> XcuitestEnvironment:
    """The lifecycle environment, kept so the fault can reach the runner's port and host process."""
    return XcuitestEnvironment("xcuitest", UDID, simctl._real_run)


@pytest.fixture(scope="module")
def driver(_eff: Effective, environment: XcuitestEnvironment) -> base.Driver:
    driver, _readiness = launch_driver(
        UDID,
        _eff,
        "xcuitest",
        extra_env={"SHOWCASE_UITEST": "1"},
        environment=environment,
    )
    return driver


def _probe_listeners(port: int) -> tuple[list[str], str]:
    """The distinct pids listening on *port*, paired with how `lsof` itself fared.

    `lsof` exits non-zero both for "no match" and for its own failures, so the second element carries
    its exit code and stderr: a caller reporting an empty result must be able to say which it was, so
    an unusable `lsof` never reads as a runner that never came up.
    """
    try:
        probe = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        pytest.fail(
            "`lsof` is not on PATH, so the runner's pid cannot be resolved and no fault can be "
            "injected; it ships with macOS, the only host this suite runs on"
        )
    # Deduplicated: `lsof -t` prints one line per matching file descriptor, so a process holding the
    # listening socket on more than one fd repeats its own pid. Counting raw lines would read that as
    # several listeners and fail a perfectly ordinary environment.
    return sorted(set(probe.stdout.split())), (
        f"lsof exit {probe.returncode}, stderr {probe.stderr.strip()!r}"
    )


def _listening_pid_or_none(port: int) -> int | None:
    """The pid holding the runner's loopback port, or None when nothing holds it.

    Found by port rather than by process name so a rename of the runner target cannot turn this suite
    into one that silently signals nothing. The port is bound by one process (`_allocate_port` hands
    the runner an ephemeral port of its own), so more than one listener means the environment is not
    the one this suite thinks it is and the fault would land somewhere unintended — that still fails
    here. Only an unbound port is a value rather than a failure: the killed case's premise is an
    exited runner, which it can legitimately reach already satisfied.
    """
    found, _diagnosis = _probe_listeners(port)
    if not found:
        return None
    # More than one *distinct* pid means this is not the environment the suite thinks it is, and the
    # fault would land somewhere unintended.
    assert len(found) == 1, f"{len(found)} processes listen on the runner port {port}: {found}"
    return int(found[0])


def _listening_pid(port: int) -> int:
    """`_listening_pid_or_none`, for a fault whose premise is a runner still holding the port."""
    found, diagnosis = _probe_listeners(port)
    assert found, (
        f"nothing is listening on the runner port {port}; the fault would hit no process "
        f"({diagnosis})"
    )
    assert len(found) == 1, f"{len(found)} processes listen on the runner port {port}: {found}"
    return int(found[0])


def test_a_frozen_runner_is_classified_as_a_crash_and_the_call_re_issued(
    driver: base.Driver,
    environment: XcuitestEnvironment,
    shrunk_retry_budget: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pid = _listening_pid(environment._runner_port)
    os.kill(pid, signal.SIGSTOP)
    # Release from a separate thread: the driver call below blocks on the frozen socket for the whole
    # retry budget, so nothing on this thread could lift the freeze in time. Armed only once the stop
    # landed, so a failed signal never leaves a timer to fire at a pid this test did not stop.
    release = threading.Timer(_freeze_hold_s(), os.kill, args=(pid, signal.SIGCONT))
    release.start()
    try:
        with caplog.at_level(logging.WARNING, logger=_RECOVERY_LOGGER):
            try:
                elements: list[base.Element] | None = driver.query()
            except xcuitest.XcuitestRunnerCrashError:
                # Not a failure of the mechanism: XCTest reclaims a runner it cannot reach, whatever
                # lifted the freeze (its own log says "Restarting after unexpected exit, crash, or
                # test timeout"). The re-issue then meets an already-exited process. What the freeze
                # is here to prove — that a *hung* socket is classified as a crash and recovery
                # re-issues — has already happened by then, and the assertions below read it off the
                # log. Whether the re-issued call also lands on a live app is XCTest's to decide.
                elements = None
    finally:
        release.cancel()
        # Covers the timer not having fired yet. Suppressed because a runner that died during the
        # freeze must not replace the real failure above with a `ProcessLookupError` from cleanup.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)

    # The hung connection specifically. Only a stopped process produces it — a killed one refuses the
    # connection outright — so this is what separates this fault from the next test's, and pinning it
    # keeps a freeze that silently degraded into a kill from passing as the frozen case.
    assert "retrying: timed out" in caplog.text, (
        "the frozen runner never produced a hung connection: the transport saw some other failure, "
        "so the socket-timeout path this fault exists to drive was not the one exercised"
    )
    # Without this the test would also pass on a freeze the transport never noticed — a green run
    # proving the recovery path nothing.
    assert "recovered from a mid-run crash" in caplog.text, (
        "the frozen runner never reached crash recovery: the transport either rode the freeze out "
        "inside its retry budget or classified the hung connection as something other than a crash"
    )
    # Only when the re-issue actually reached the app — see the suppression above for when it cannot.
    if elements is not None:
        assert elements, (
            "the recovered read returned no elements, so the re-issue did not reach the app"
        )


def test_a_killed_runner_fails_with_a_crash_diagnosis_not_an_unrelated_timeout(
    driver: base.Driver, environment: XcuitestEnvironment, shrunk_retry_budget: None
) -> None:
    # An exited runner is this test's premise, not its fault to cause: the frozen case ahead of it
    # leaves one behind whenever XCTest reclaimed the stopped process, and which fault got it there
    # changes nothing about what is asserted below. So kill whatever still holds the port, and treat
    # an already-free port as the premise arriving pre-satisfied rather than as a broken environment.
    pid = _listening_pid_or_none(environment._runner_port)
    if pid is not None:
        os.kill(pid, signal.SIGKILL)
    # Kill the `xcodebuild` host too, so `runner_alive` reports the process gone and recovery fails
    # fast on that rather than polling the dead port for the whole recovery window.
    proc = environment._runner_proc
    assert proc is not None, "the environment holds no runner process to kill"
    proc.kill()
    # Bounded: an unbounded wait would hang this metered macOS job indefinitely if `xcodebuild` (or a
    # wrapper of it) does not reap promptly after the kill. Failing here with that as the diagnosis
    # beats spending the job's whole timeout on it.
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the `xcodebuild` runner host did not exit within 30s of SIGKILL, so `runner_alive` "
            "cannot yet report the process gone and this test's premise does not hold"
        )

    with pytest.raises(xcuitest.XcuitestRunnerCrashError) as raised:
        driver.query()

    # A `BackendCrashError` is what lets the run pipeline recover backend-agnostically — leasing a
    # fresh device and re-running the scenario — so the classification, not just the message, is the
    # property under test.
    assert isinstance(raised.value, base.BackendCrashError)
    # The BE-0319 diagnosis specifically, not one of the other crash messages: an exited process is
    # what recovery must name here, and reaching the "did not recover within Ns" branch instead would
    # mean it polled a dead port for the whole recovery window.
    assert "process exited" in str(raised.value)
