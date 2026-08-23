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
from collections.abc import Callable, Iterator
from pathlib import Path

import ondevice_evidence
import pytest
from backend_crash_recovery import LeaseHolder, LeaseTeardown
from driver_conformance import (
    ConformanceHarness,
    DriverConformanceContract,
    OnDeviceConformanceHarness,
)
from xcuitest_lease import xcuitest_lease_launch

from bajutsu import simctl
from bajutsu.config import Effective, ios_bundle_id, load_config, resolve
from bajutsu.drivers import base
from bajutsu.evidence import intervals

# A resident-runner crash mid-suite (a `base.BackendCrashError` — the `XcuitestRunnerCrashError` that
# reddened PR #1405, whether raised by a test's actuation or by a query driving the bring-up/readiness
# path) is infrastructure, not a verdict: the `backend_crash_recovery` plugin (BE-0334) re-leases a
# fresh device off the `_backend_launch` fixture below and re-runs the affected test, exactly as
# `bajutsu run` recovers a `BackendCrashError`. A contract violation is not a `BackendCrashError`, so
# it is never retried — it keeps failing immediately. (A cold spawn that never comes up is already
# retried by the spawn layer (BE-0319) and stays terminal past it, as in the pipeline.)
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


def _spec_path(eff: Effective) -> Path:
    """The `conformance-spec.txt` in the installed app's Documents dir (the reseed channel)."""
    container = simctl._real_run(simctl.data_container_cmd(UDID, ios_bundle_id(eff)), None).strip()
    return Path(container) / "Documents" / "conformance-spec.txt"


@pytest.fixture(scope="module")
def _eff() -> Effective:
    return _effective()


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


class TestXcuitestDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, _eff: Effective, _backend_lease_holder: LeaseHolder) -> ConformanceHarness:
        # Read the driver off the holder each test: crash-free, it is the one shared lease (the module
        # scope's amortization); after a crash, the plugin has re-leased, so this is the fresh device.
        return _OnDeviceHarness("xcuitest", _backend_lease_holder.driver, _spec_path(_eff))

    # Marked, not skipped from inside the body: a body-level `pytest.skip()` still lets pytest set
    # every fixture up first, so the autouse `_evidence` above would start and stop a video recording
    # and a device log — real `simctl` work on the shared Simulator — for a case that never runs. The
    # marker skips at setup, before any fixture runs, so `harness` here stays the contract's
    # signature without ever being constructed, and this costs the lease nothing.
    @pytest.mark.skip(reason="BE-0339 Unit 6: not yet realized on-device, see the docstring")
    def test_a_tap_lands_on_the_element_the_selector_named(
        self, harness: ConformanceHarness
    ) -> None:
        """Not yet realized on-device (BE-0339 Unit 6).

        The Android twin of this case degraded the emulator's UI thread for the rest of the suite
        after adding the tap-mirror pair to ConformanceScreen — see the matching skip in
        `test_driver_conformance_ondevice_android.py` for the full account. `ConformanceView.swift`'s
        tap-mirror elements were reverted alongside Compose's without ever getting a real iOS
        Simulator run (this PR's iOS signal never got past cancellation artifacts from superseding
        pushes), so shipping them untested here would repeat the same unverified-device-change
        mistake rather than avoid it. The case still runs deterministically against `FakeDriver` and
        Playwright (`tests/test_driver_conformance.py`, `tests/test_driver_conformance_web.py`).
        """
