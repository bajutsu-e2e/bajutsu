"""Inject a real runner fault and watch the XCUITest resilience layers survive it (BE-0305).

Two mechanisms exist to keep a run honest when the resident BajutsuRunner misbehaves: BE-0207's
transient retry, which re-issues an idempotent call over a blip, and BE-0287's crash recovery, which
waits out a runner that went away mid-run and re-issues, or fails loudly on a write it must not
re-send. Their unit tests raise a synthetic exception from a nested closure. That proves the control
flow runs, and nothing about whether the real failure mode reaches it: a real runner does not raise a
Python exception, it stops answering a socket — a hang, a refused connect, a partial write — and
whether that lands in the retry seam, in crash recovery, or in neither depends on the real socket
timeouts and the real relaunch latency. This lane injects the real thing.

The fault is a signal to the process that actually serves the channel — the runner's in-Simulator host
process, found by the device's own UDID so a host running two lanes never signals the other device's
runner. `SIGSTOP` freezes it with its listening socket still accepting, which is what a wedged runner
looks like from the host: the connect succeeds and the request hangs. How long it stays frozen decides
which layer sees it, so no case guesses a duration — each lifts the fault on the driver's own log
record that it reached the layer under test (`tests/fault_injection.py`). `SIGKILL` covers the case no
recovery can absorb, and asserts the run stops on an honest crash diagnosis rather than an unrelated
timeout.

Runs in the on-device iOS path (`ios-e2e.yml`), never in `make check`: the `ondevice` marker is
deselected by the gate's default `-m 'not web and not ondevice'`, and a module-level skip drops it
whenever `BAJUTSU_FAULT_INJECTION_UDID` is unset — the fast gate's state. Deliberately *not* marked
`backend_crash_recovery`: that plugin re-leases and re-runs a test whose backend crashed, and here the
crash is the assertion, so its recovery would hide the very thing under test. The lane leases through
the plugin's `LeaseHolder` all the same, so the test that kills its runner discards that lease and the
next test cold-respawns rather than inheriting a dead one.

Run serially (`-n0`): the cases share one Simulator and signal a process found by its UDID, so
parallel workers would freeze each other's runner. The `ios-e2e.yml` job passes `-n0`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import fault_injection
import pytest
from backend_crash_recovery import LeaseHolder, LeaseTeardown
from driver_conformance import OnDeviceConformanceHarness
from xcuitest_lease import xcuitest_lease_launch

from bajutsu.config import Effective, load_config, resolve
from bajutsu.drivers import base
from bajutsu.drivers.xcuitest import XcuitestRunnerCrashError

pytestmark = pytest.mark.ondevice

# The E2E workflow provisions a booted Simulator with the showcase app and signals it here; absent
# (any Linux box, the fast gate), skip the whole module. The `ondevice` marker also deselects it, so
# this is belt-and-braces — the lane never runs, or errors, off an on-device host. A knob of its own
# rather than the conformance lane's, so the two jobs are enabled independently.
_udid = os.environ.get("BAJUTSU_FAULT_INJECTION_UDID")
if not _udid:
    pytest.skip(
        "XCUITest fault injection needs BAJUTSU_FAULT_INJECTION_UDID (a booted Simulator with the "
        "showcase app installed) — it runs in the E2E workflow, never the fast gate",
        allow_module_level=True,
    )
UDID: str = _udid  # narrowed by the skip above; a plain str for the fixtures below

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-swiftui"  # the a11y app: its identifiers surface for XCUITest

# Boot into (the empty) conformance mode like the conformance lane, for the same reason: the readiness
# probe then snapshots a trivial screen instead of quiescing the heavy 5-tab UI on a cold CI
# Simulator. Its always-present marker is the element every case reads back after the fault.
_CONFORMANCE_ENV = {"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": ""}
_MARKER: base.Selector = {"id": OnDeviceConformanceHarness.READY_ID}

_CHANNEL_LOGGER = "bajutsu.xcuitest.channel"
# Fragments of the channel's own log records. They are what the cases lift the fault on and assert
# against, so a reworded record fails the assertion loudly rather than silently weakening the lane.
_RETRY = "retrying"  # the BE-0207 seam re-issuing an attempt
_CRASH = "past the retry budget"  # BE-0287 declaring a mid-run crash
_RECOVERED = "recovered from a mid-run crash"

# The runner's host process runs from the device's own CoreSimulator container, so its command line
# carries both the UDID and the runner app bundle. Matching on both is what scopes a signal to this
# device.
_RUNNER_BUNDLE = "-Runner.app/"

# Bounds the wait for a log record that should arrive within seconds of the fault. Generous enough for
# a loaded CI host to work through the channel's own budgets (a read's socket window is 15s and the
# retry seam spends three of them before declaring a crash), and short enough that a record that never
# comes fails the case instead of hanging the job.
_TRIGGER_TIMEOUT = 180.0


def _effective() -> Effective:
    # A raw resolve() leaves the config's relative appPath / testRunner config-relative; rebase
    # against the config's own directory (unconfined, like a local config, BE-0242) so launch_driver
    # sees the same absolute paths the CLI would — mirrors the on-device conformance modules.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


def _runner_pids() -> list[str]:
    """This device's runner host processes — the ones that serve the channel's loopback socket.

    `pgrep` exits 1 when nothing matched, which `_require_runner_pids` turns into its own failure; any
    other status means the search itself broke, and reporting that as "no runner" would send the reader
    hunting for a dead lease instead of a broken command.
    """
    found = subprocess.run(["pgrep", "-fl", UDID], capture_output=True, text=True)
    if found.returncode not in (0, 1):
        pytest.fail(f"pgrep failed ({found.returncode}): {found.stderr.strip()}")
    return [
        line.split(maxsplit=1)[0] for line in found.stdout.splitlines() if _RUNNER_BUNDLE in line
    ]


def _require_runner_pids() -> list[str]:
    """The runner's PIDs, failing the case when there are none: an unsignalled fault proves nothing.

    A green run with no fault injected would be the worst outcome this lane could produce — it would
    read as "the resilience layers hold" while nothing was ever tested.
    """
    pids = _runner_pids()
    if not pids:
        pytest.fail(
            f"no resident runner process found for {UDID}: nothing to inject a fault into, so the "
            "case cannot prove anything (has the lease's runner already died?)"
        )
    return pids


def _inject(pids: list[str], signal: str) -> None:
    """Send the fault signal, failing the case when it did not land on a single process.

    A `kill` that is refused (a recycled pid, a permission the CI host withholds) leaves the runner
    healthy, and the case would then fail on "the layer never engaged" while the real cause — that no
    fault was ever injected — sat in a discarded stderr.
    """
    refused = []
    for pid in pids:
        sent = subprocess.run(["kill", signal, pid], capture_output=True, text=True, check=False)
        if sent.returncode != 0:
            refused.append(f"{pid}: {sent.stderr.strip() or f'exit {sent.returncode}'}")
    if len(refused) == len(pids):
        pytest.fail(
            f"could not {signal} the runner, so no fault was injected — {'; '.join(refused)}"
        )


def _release(pids: list[str]) -> None:
    # The counterpart to `_inject`, and deliberately lenient: it runs on every exit path, and by then
    # the runner may legitimately be gone (a killed case, a runner that died on its own), so a refused
    # `-CONT` is the expected outcome rather than a failure to report.
    for pid in pids:
        subprocess.run(["kill", "-CONT", pid], capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def _eff() -> Effective:
    return _effective()


@pytest.fixture(scope="module")
def _backend_launch(_eff: Effective) -> Callable[[], tuple[base.Driver, LeaseTeardown]]:
    # A cold spawn, leased lazily by the `LeaseHolder` the `backend_crash_recovery` plugin supplies:
    # crash-free it is the one shared lease, and the killed-runner case discards it so the next case
    # respawns onto a fresh device instead of inheriting the dead runner. Fresh environment per
    # lease + its teardown, so discard reaches the runner process (BE-0342).
    return xcuitest_lease_launch(UDID, _eff, extra_env=_CONFORMANCE_ENV)


@pytest.fixture
def lease(_backend_lease_holder: LeaseHolder) -> LeaseHolder:
    """The module's lease holder, under a name a test can take as a parameter."""
    return _backend_lease_holder


@pytest.fixture
def driver(lease: LeaseHolder) -> base.Driver:
    # Read the driver off the holder per case, so a case following the killed-runner one gets the
    # freshly re-leased device rather than a dead handle.
    return lease.driver


def _read_back_the_marker(driver: base.Driver) -> None:
    """Assert the post-fault read describes the real screen, not a degraded stand-in."""
    base.resolve_unique(driver.query(), _MARKER)


def test_a_real_socket_hang_is_absorbed_by_the_transient_retry(driver: base.Driver) -> None:
    # The BE-0207 seam against the real failure mode: a frozen runner accepts the connection and then
    # answers nothing, so the attempt times out on the socket rather than raising. Released on the
    # seam's own "retrying" record, the next attempt lands on a live runner and the read succeeds —
    # the blip is absorbed, and (asserted below) never escalates to crash recovery.
    _read_back_the_marker(driver)  # the pre-fault screen, and it warms the channel
    frozen = _require_runner_pids()
    with fault_injection.watch(_CHANNEL_LOGGER, _RETRY) as log:
        _inject(frozen, "-STOP")
        try:
            with fault_injection.lifted_when_reached(
                log, lambda: _release(frozen), timeout=_TRIGGER_TIMEOUT
            ):
                elements = driver.query()
        finally:
            _release(frozen)  # never leave the runner frozen for the rest of the lane
    assert log.mentions(_RETRY), f"the retry seam never reported an attempt:\n{log.report()}"
    assert not log.mentions(_CRASH), (
        f"a hang lifted at the first retry escalated to crash recovery:\n{log.report()}"
    )
    base.resolve_unique(elements, _MARKER)


def test_a_hang_past_the_retry_budget_is_ridden_out_by_crash_recovery(
    lease: LeaseHolder, driver: base.Driver
) -> None:
    # The BE-0287 path against the real failure mode. Held frozen until the channel has spent its
    # whole retry budget and declared a mid-run crash, the runner comes back while recovery is polling
    # `/health` — so the idempotent read is re-issued and returns the real screen, exactly the "ride
    # out a runner that went away and came back" contract, driven by a real socket-level fault.
    try:
        _read_back_the_marker(driver)
        frozen = _require_runner_pids()
        with fault_injection.watch(_CHANNEL_LOGGER, _CRASH) as log:
            _inject(frozen, "-STOP")
            try:
                with fault_injection.lifted_when_reached(
                    log, lambda: _release(frozen), timeout=_TRIGGER_TIMEOUT
                ):
                    elements = driver.query()
            finally:
                _release(frozen)
        assert log.mentions(_CRASH), f"crash recovery was never reached:\n{log.report()}"
        assert log.mentions(_RECOVERED), (
            f"crash recovery never reported re-issuing the call:\n{log.report()}"
        )
        base.resolve_unique(elements, _MARKER)
    finally:
        # A read that recovers by re-issuing can come back on a freshly re-spawned runner. Discard
        # the module lease regardless of outcome (including an assertion failure above) so the next
        # case never inherits a half-turned-over resident (old port / dead process) and fails before
        # it injects its own fault.
        lease.invalidate()


def test_a_killed_runner_fails_loudly_with_a_crash_diagnosis(lease: LeaseHolder) -> None:
    # The fault no recovery can absorb: the host process is gone, so nothing answers the channel again
    # on that port. The contract is the diagnosis, not the latency — a `BackendCrashError` naming a
    # mid-run crash, which is what lets the run pipeline (and the conformance lane's plugin) recognise
    # it as infrastructure and lease a fresh device, where a bare timeout would read as a real verdict.
    driver = lease.driver
    _read_back_the_marker(driver)
    _inject(_require_runner_pids(), "-KILL")
    try:
        with pytest.raises(base.BackendCrashError) as crash:
            driver.query()
    finally:
        # The lease is dead whatever the outcome: discard it so the next case cold-respawns.
        lease.invalidate()
    assert isinstance(crash.value, XcuitestRunnerCrashError)
    # Two of the layer's diagnoses are legitimate here, and which one fires is a property of the host,
    # not of the fault: recovery fails fast when the runner's `xcodebuild` parent has already exited
    # ("the runner process exited mid-run"), and otherwise polls its window out ("crashed mid-run and
    # did not recover"). Measured on a Simulator, the parent outlives the killed host process, so the
    # second one fires. Both name a mid-run runner fault, which is the contract worth pinning — pinning
    # either exact sentence would make the case fail on a host that took the other branch.
    assert "mid-run" in str(crash.value), (
        f"the diagnosis does not name a mid-run runner fault: {crash.value}"
    )
