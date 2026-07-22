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
from pathlib import Path

import pytest
from driver_conformance import (
    ConformanceHarness,
    DriverConformanceContract,
    OnDeviceConformanceHarness,
)

from bajutsu import simctl
from bajutsu.config import Effective, ios_bundle_id, load_config, resolve
from bajutsu.drivers import base
from bajutsu.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

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
def _xcuitest_driver(_eff: Effective) -> base.Driver:
    driver, _readiness = launch_driver(UDID, _eff, "xcuitest", extra_env=_CONFORMANCE_ENV)
    return driver


class TestXcuitestDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, _eff: Effective, _xcuitest_driver: base.Driver) -> ConformanceHarness:
        return _OnDeviceHarness("xcuitest", _xcuitest_driver, _spec_path(_eff))
