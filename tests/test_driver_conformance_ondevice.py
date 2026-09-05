"""Run the driver conformance contract (BE-0114) against the on-device iOS backend (XCUITest).

Unlike the FakeDriver suite (browser-free, on the fast Linux gate) and the Playwright suite (web
CI), this drives the real iOS Simulator backend: XCUITest via the resident BajutsuRunner. The point
of the suite is to catch drift on the backend's *own* query / act code, which only surfaces against
the real actuator — so it needs a booted Simulator with the showcase a11y app installed and the
built runner. It runs in the on-device E2E path (`ios-e2e.yml`), never in `make check`: an
`ondevice` pytest marker (deselected by the gate's default `-m 'not web and not ondevice'`) keeps it
out, and a module-level skip drops it whenever `BAJUTSU_CONFORMANCE_UDID` is unset — the fast gate's
state.

Each conformance screen is realized on-device by writing a spec file the app polls (BE-0114): the
app launches into conformance mode (SHOWCASE_CONFORMANCE) and re-renders exactly the identifiers
the file names — duplicates, the empty set, unique — so the same contract that seeds FakeDriver's
`screen=` and Playwright's HTML drives the real device. Reseeding writes a file rather than
relaunching or deep-linking on purpose: `simctl openurl` for a custom scheme raises iOS's "Open in
app?" system dialog, and relaunching per screen crashes the resident XCUITest runner after a
handful of `app.launch()` cycles. A file write touches neither, so both backends stay stable across
the whole suite.

Run serially (`-n0`): the suite reseeds one shared Simulator via one spec file, so parallel xdist
workers would clobber each other's screen. The `ios-e2e.yml` job passes `-n0`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import ondevice_evidence
import pytest
from _pytest.nodes import Node
from backend_crash_recovery import LeaseHolder, LeaseTeardown, record_absorbed_stall
from driver_conformance import (
    ConformanceHarness,
    DriverConformanceContract,
    OnDeviceConformanceHarness,
    row_offsets,
)
from ondevice_spec_path import SpecPathMemo, read_data_container
from xcuitest_lease import xcuitest_lease_launch

from bajutsu.common.backend_cli import simctl
from bajutsu.common.config import Effective, ios_bundle_id, load_config, resolve
from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals
from bajutsu.common.orchestrator.actions.handlers.scroll import (
    _AXIS,
    _STEP_FRACTION,
    _step_endpoints,
    _viewport,
)

# A resident-runner crash mid-suite (a `base.BackendCrashError` — the `XcuitestRunnerCrashError` that
# reddened PR #1405, whether raised by a test's actuation or by a query driving the bring-up/readiness
# path) is infrastructure, not a verdict: the `backend_crash_recovery` plugin (BE-0334) re-leases a
# fresh device off the `_backend_launch` fixture below and re-runs the affected test, exactly as
# `bajutsu run` recovers a `BackendCrashError`. A contract violation is not a `BackendCrashError`, so
# it is never retried — it keeps failing immediately. Nor is a wedged CoreSimulator
# (`simctl.DeviceTimeout`): the plugin names that a host fault and lets the failure stand, rather than
# rebuilding the device to answer a stall that clears on its own (BE-0378). (A cold spawn that never
# comes up is already retried by the spawn layer (BE-0319) and stays terminal past it, as in the
# pipeline.)
pytestmark = [pytest.mark.ondevice, pytest.mark.backend_crash_recovery]

# The E2E workflow provisions a booted Simulator with the showcase app and signals it here; absent
# (any Linux box, the fast gate), skip the whole module. The `ondevice` marker also deselects it,
# so this is belt-and-braces — the suite never runs, or errors, off an on-device host.
_udid = os.environ.get("BAJUTSU_CONFORMANCE_UDID")
if not _udid:
    pytest.skip(
        "on-device conformance needs BAJUTSU_CONFORMANCE_UDID (a booted Simulator with the "
        "showcase app installed) — it runs in the E2E workflow, never the fast gate",
        allow_module_level=True,
    )
UDID: str = _udid  # narrowed by the skip above; a plain str for the fixtures below

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-swiftui"  # the a11y app: its identifiers surface for XCUITest


class _OnDeviceHarness(OnDeviceConformanceHarness):
    """Realizes each conformance screen by writing the spec file the app polls.

    The shared `with_screen` / condition-backed `_await_screen` live in the base
    (`OnDeviceConformanceHarness`); this backend supplies only `_realize` — an atomic write of the
    identifier spec to the app's `conformance-spec.txt`. A file write rather than a relaunch, so the
    resident XCUITest runner survives the whole suite.
    """

    def __init__(self, backend: str, driver: base.Driver, spec_path: Path) -> None:
        super().__init__(backend, driver)
        self._spec_path = spec_path

    def _realize(self, ids: list[str]) -> None:
        # Atomic write (temp + replace): the app polls this file, so a partial read would render a
        # garbled screen. The Documents dir may not exist until first written, hence mkdir.
        self._spec_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._spec_path.with_suffix(".tmp")
        tmp.write_text(",".join(ids))
        tmp.replace(self._spec_path)


def _effective() -> Effective:
    # A raw resolve() bypasses `_load_effective_with_source`, so the config's relative appPath /
    # testRunner would stay config-relative and miss where they point from here. Rebase against the
    # config's own directory (unconfined, like a local config, BE-0242) so launch_driver sees the
    # same absolute paths the CLI would.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


def _spec_path(eff: Effective, node: Node) -> Path:
    """The `conformance-spec.txt` in the installed app's Documents dir (the reseed channel).

    Args:
        node: The suite's own module node, which carries a stall the read absorbed into the job log
            and the uploaded report — this read belongs to no single test, so it reports as the
            module it prepares.
    """
    container = read_data_container(
        UDID,
        ios_bundle_id(eff),
        simctl.real_run,
        lambda reason: record_absorbed_stall(node, reason),
    )
    return Path(container) / "Documents" / "conformance-spec.txt"


@pytest.fixture(scope="module")
def _eff() -> Effective:
    return _effective()


@pytest.fixture(scope="module")
def _spec_paths(request: pytest.FixtureRequest, _eff: Effective) -> SpecPathMemo:
    # Module-scoped, so the device read below is paid once per lease rather than once per test — the
    # exposure BE-0378 removes. The memo re-reads on its own whenever the lease changes.
    node = request.node
    return SpecPathMemo(lambda: _spec_path(_eff, node))


# Boot the fixture straight into (the empty) conformance mode, not the normal tab app: this enters
# ConformanceView and starts the app's spec-file polling, and the launch readiness probe then
# snapshots a trivial screen (one marker element) — which matters for XCUITest, whose first snapshot
# waits for the app to idle (quiescing the heavy 5-tab UI on a cold CI Simulator can time out).
# SHOWCASE_UITEST (also supplied by the target's launchEnv) is set explicitly for clarity.
_CONFORMANCE_ENV = {"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": ""}


@pytest.fixture(scope="module")
def _backend_launch(_eff: Effective) -> Callable[[], tuple[base.Driver, LeaseTeardown]]:
    # A cold spawn: the `backend_crash_recovery` plugin calls this to lease the shared device, and
    # again to re-lease a fresh one after a crash. Each call builds a fresh environment so the
    # respawn stays a cold spawn (not an in-place warm resume) and returns that environment's
    # teardown alongside the driver — `invalidate()` reaches the runner process, not a missing
    # `driver.close()` (BE-0342).
    return xcuitest_lease_launch(UDID, _eff, extra_env=_CONFORMANCE_ENV)


@pytest.fixture(autouse=True)
def _evidence(request: pytest.FixtureRequest) -> Iterator[None]:
    """Video + deviceLog for this case, kept only on failure (the CI job otherwise has neither).

    Also correct across an infra-fault retry: `backend_crash_recovery` re-runs this whole item (and
    every function-scoped fixture, this one included) on a crash, so a crashed attempt's recording is
    cleared by the next attempt's (`capture()`'s own leading `rmtree`) before that attempt records its
    own — exactly right, since only the terminal (published) attempt's evidence should survive.
    """
    yield from ondevice_evidence.capture(
        UDID,
        "conformance-xcuitest",
        request,
        start_video=ondevice_evidence.xcuitest_video,
        start_log=intervals.start_device_log,
    )


@pytest.fixture
def harness(_backend_lease_holder: LeaseHolder, _spec_paths: SpecPathMemo) -> ConformanceHarness:
    """The harness every test in this module drives, rebuilt per test off the shared lease.

    Read the driver off the holder each test: crash-free, it is the one shared lease (the module
    scope's amortization); after a crash, the plugin has re-leased, so this is the fresh device.
    `for_lease` leases too, so the two agree on which installation the spec path names. Module-level
    rather than one copy per test class, so the lease and spec-path wiring has a single definition
    to change.
    """
    driver = _backend_lease_holder.driver
    return _OnDeviceHarness("xcuitest", driver, _spec_paths.for_lease(_backend_lease_holder))


class TestXcuitestDriverConformance(DriverConformanceContract):
    """The contract, collected against this module's shared-lease `harness` fixture."""


#: How long after `scroll` returns the content must stay put for the read to count as settled
#: (BE-0400). Generous against what the gesture leaves behind: the measurement that motivated this
#: found the content already at rest more than 190 ms *before* the driver returned, in twelve of
#: twelve gestures. It is deliberately longer than the ~900 ms the scroll indicator takes to fade,
#: even though this compares element frames rather than pixels and never sees the indicator — so a
#: future reader need not wonder whether the window was cut to dodge it.
_SETTLE_WINDOW_S = 1.0

#: How far two reads of a row may differ and still count as the same position, in points. Absorbs
#: sub-pixel rounding between two snapshots without admitting real residual motion, which the
#: uncorrected gesture expressed in tens of points, not fractions of one.
_SETTLE_EPSILON_PT = 0.5

#: How far a row must have travelled for the step to count as having moved the content at all, in
#: points. The guard against a vacuous pass, so it is deliberately a different threshold from the
#: one above: a gesture that moved nothing would hold still afterwards and prove nothing.
_MOVED_PT = 1.0


class TestXcuitestScrollSettles:
    """`scroll` returns only once the content has stopped — the property the realized-travel contract rests on.

    Kept in this file rather than one of its own so it runs under the `conformance (xcuitest)` job
    the way every other on-device case does — that job names its test files explicitly, and a new
    file would need both a deliberate wiring change to a required check and a second cold lease on a
    metered runner. It sits outside `DriverConformanceContract` because it is scoped to the backend
    this item measured: Android reaches the same guarantee through a different mechanism (a marked
    read that a query waits for, BE-0332), so stating it as a shared contract would assert one
    backend's timing of another's.

    The property matters because every conclusion the `scroll` loop draws — the target's position,
    whether the region moved, how far a step travelled — comes from the tree read right after the
    driver returns. If the content were still decelerating then, each of those would describe a
    screen the run never actually stopped on.
    """

    def test_a_scroll_returns_only_once_the_content_is_at_rest(
        self, harness: ConformanceHarness
    ) -> None:
        driver = harness.scrollable_screen()
        elements = driver.query()
        viewport = _viewport(driver, elements)
        axis = _AXIS["down"]
        bounds: base.Frame = (0.0, 0.0, viewport[0], viewport[1])
        before = row_offsets(elements, bounds, axis)
        frm, dest = _step_endpoints(elements, "down", None, viewport, _STEP_FRACTION)
        driver.scroll(frm, dest)
        settled = row_offsets(driver.query(), bounds, axis)
        # Two guards against a vacuous pass, each asserted separately so its failure says which one
        # fired: a read sharing no row with the first cannot be compared to it — the signature of a
        # gesture that flung, not of one that did nothing — and a gesture that moved nothing would
        # hold still trivially. Past both, a failure below is about motion after the return and
        # nothing else.
        common = before.keys() & settled.keys()
        assert common, "the read after the step shared no row with the one before it"
        assert any(abs(before[i] - settled[i]) > _MOVED_PT for i in common), (
            "the step moved no row, so a still screen proves nothing about settling"
        )
        deadline = time.monotonic() + _SETTLE_WINDOW_S
        while time.monotonic() < deadline:
            # No sleep between reads: each `query()` is a full snapshot and costs real time, so the
            # window is sampled as fast as the backend can answer — the densest evidence available,
            # and a wait on nothing would only thin it.
            again = row_offsets(driver.query(), bounds, axis)
            shared = settled.keys() & again.keys()
            assert shared, "consecutive reads of a still screen shared no row"
            drift = max(abs(settled[i] - again[i]) for i in shared)
            assert drift <= _SETTLE_EPSILON_PT, (
                f"the content moved {drift:.1f}pt after `scroll` returned, so the read the loop "
                "takes next describes a screen still in motion"
            )
