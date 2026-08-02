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
   certainly been spent, kept close to that budget rather than padded, so recovery finds the runner
   healthy and re-issues.
2. **Killed** — `SIGKILL` the runner and its `xcodebuild` host process. `runner_alive` then reports
   the process gone and recovery fails fast with the diagnosis naming that, instead of polling a dead
   port for the whole recovery window.

The killed case leaves no runner behind, so it is written last and must run after the frozen one.
Source order plus `-n0` is what guarantees that, which is why both callers pass `-n0` rather than
inheriting the repo's default `-n auto`.

Running against a real, frozen socket costs real wall time — roughly the transport budget plus a
margin for the first fault — which no injected-clock unit test pays. That is inherent to the fault
being real, and is why the lane lands as a non-gating signal first (`fault (xcuitest)` in
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

# How long the freeze holds before it is released: the transport's own worst-case retry budget (a
# `GET`, the method `query` issues) plus a small margin. Read from the transport rather than
# restated here, so re-tuning the retry loop re-tunes the fault with it. The margin stays small on
# purpose: this suite drives one request at a time, never several concurrently, so the kernel's
# accept backlog is never full and `connect()` is always served instantly — only the response read
# actually blocks for the socket timeout, which `_retry_budget_seconds` already accounts for. A
# margin wider than that buys nothing and costs real risk the other way: SIGSTOP is not free, and
# holding a real XCTest host stopped far past the failure it's proving risks losing the runner
# outright rather than letting it resume (observed: doubling this held the runner past recovery's
# window and left nothing listening for the next test).
_RETRY_BUDGET_S = xcuitest._retry_budget_seconds("GET")
_FREEZE_HOLD_S = _RETRY_BUDGET_S + 5.0
# Released after the retry loop has certainly given up, and before recovery stops waiting: outside
# either bound the suite would silently test something else (the retry absorbing the freeze, or the
# never-recovered branch), so a re-tune that breaks the window fails at import rather than on-device.
assert _RETRY_BUDGET_S < _FREEZE_HOLD_S < _RETRY_BUDGET_S + xcuitest._RECOVERY_TIMEOUT_SECONDS

_RECOVERY_LOGGER = "bajutsu.xcuitest.channel"


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


def _listening_pid(port: int) -> int:
    """The pid holding the runner's loopback port — the process a fault must signal.

    Found by port rather than by process name so a rename of the runner target cannot turn this suite
    into one that silently signals nothing. The port is bound by one process (`_allocate_port` hands
    the runner an ephemeral port of its own), so more than one listener means the environment is not
    the one this suite thinks it is and the fault would land somewhere unintended.
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
    found = sorted(set(probe.stdout.split()))
    # `lsof` exits non-zero both for "no match" and for its own failures, so the diagnosis carries
    # its exit code and stderr: an unusable `lsof` must not read as a runner that never came up.
    assert found, (
        f"nothing is listening on the runner port {port}; the fault would hit no process "
        f"(lsof exit {probe.returncode}, stderr {probe.stderr.strip()!r})"
    )
    # More than one *distinct* pid means this is not the environment the suite thinks it is, and the
    # fault would land somewhere unintended.
    assert len(found) == 1, f"{len(found)} processes listen on the runner port {port}: {found}"
    return int(found[0])


def test_a_frozen_runner_is_recovered_and_the_call_re_issued(
    driver: base.Driver, environment: XcuitestEnvironment, caplog: pytest.LogCaptureFixture
) -> None:
    pid = _listening_pid(environment._runner_port)
    os.kill(pid, signal.SIGSTOP)
    # Release from a separate thread: the driver call below blocks on the frozen socket for the whole
    # retry budget, so nothing on this thread could lift the freeze in time. Armed only once the stop
    # landed, so a failed signal never leaves a timer to fire at a pid this test did not stop.
    release = threading.Timer(_FREEZE_HOLD_S, os.kill, args=(pid, signal.SIGCONT))
    release.start()
    try:
        with caplog.at_level(logging.WARNING, logger=_RECOVERY_LOGGER):
            elements = driver.query()
    finally:
        release.cancel()
        # Covers the timer not having fired yet. Suppressed because a runner that died during the
        # freeze must not replace the real failure above with a `ProcessLookupError` from cleanup.
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)

    assert elements, (
        "the recovered read returned no elements, so the re-issue did not reach the app"
    )
    # Without this the test would also pass on a freeze the transport never noticed — a green run
    # proving the recovery path nothing.
    assert "recovered from a mid-run crash" in caplog.text, (
        "the frozen runner never reached crash recovery: the transport either rode the freeze out "
        "inside its retry budget or classified the hung connection as something other than a crash"
    )


def test_a_killed_runner_fails_with_a_crash_diagnosis_not_an_unrelated_timeout(
    driver: base.Driver, environment: XcuitestEnvironment
) -> None:
    os.kill(_listening_pid(environment._runner_port), signal.SIGKILL)
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
