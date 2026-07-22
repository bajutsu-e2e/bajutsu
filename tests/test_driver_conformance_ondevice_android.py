"""Run the driver conformance contract (BE-0114) against the on-device adb backend (Android, BE-0270).

The adb backend is the one shipped driver the contract never ran against: the Android E2E lane
(smoke / golden / visual) drives the real app with unambiguous selectors by construction, so it
cannot exercise the contract's ambiguous- and zero-match cases — exactly where a determinism-core
divergence hides (an ambiguous adb selector tapping the first match, a prime-directive-2 violation,
would pass every existing Android job). This module closes that gap the way the iOS module does for
XCUITest, driving the adb driver's *own* query / act code (over both the resident channel,
BE-0245, and the id-form matching of BE-0221) rather than the shared base alone. It runs in the
Android E2E path (`android-e2e.yml`), never in `make check`: an `ondevice` pytest marker (deselected
by the gate's default `-m 'not web and not ondevice'`) keeps it out even where adb is on PATH, and a
module-level skip drops it whenever `BAJUTSU_CONFORMANCE_SERIAL` is unset — the fast gate's state.

Each conformance screen is realized on-device by the Compose showcase's ConformanceScreen (BE-0270):
the app launches into conformance mode (SHOWCASE_CONFORMANCE) and renders exactly the identifiers the
spec names — duplicates, the empty set, unique — so the same contract that seeds FakeDriver's
`screen=`, Playwright's HTML, and iOS's spec file drives the real device. Reseeding re-launches the
`singleTask` activity with a new SHOWCASE_CONFORMANCE extra rather than pushing a file (iOS's channel)
or a deep-link: `am start` delivers the extra to the running activity via `onNewIntent` — the same
proven path the deep-link tab-switch already uses — so no file needs to land inside the app sandbox
(which `adb push` cannot reach) and the process is never relaunched between screens.

Scoped to the Compose toolkit, not Views (BE-0221): the contract seeds plain identifiers (`dup`,
`ok`, `a`, `b`, `Log`, `g`, `sel`, `s`), none carrying the `.`/`_` the two toolkits' id forms differ
over, so a Views screen would resolve the identical ids through the identical `_strip_pkg` path — no
added coverage. And a spec-driven arbitrary-id screen is only naturally expressible in Compose: its
`testTag` accepts any runtime string (surfaced as a `resource-id` via `testTagsAsResourceId`), while a
Views `resource-id` must be a compile-time `R` entry. Compose is the toolkit that can render the
contract's screens at all.

Run serially (`-n0`): the suite reseeds one shared emulator via one launch channel, so parallel xdist
workers would clobber each other's screen. The `android-e2e.yml` job passes `-n0`.
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

from bajutsu import adb
from bajutsu.config import Effective, load_config, require_android, resolve
from bajutsu.drivers import base
from bajutsu.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

# The E2E workflow provisions a booted emulator with the showcase app and signals it here; absent
# (any box without a device, the fast gate), skip the whole module. The `ondevice` marker also
# deselects it, so this is belt-and-braces — the suite never runs, or errors, off an on-device host.
_serial = os.environ.get("BAJUTSU_CONFORMANCE_SERIAL")
if not _serial:
    pytest.skip(
        "on-device adb conformance needs BAJUTSU_CONFORMANCE_SERIAL (a booted emulator/device with "
        "the showcase Compose a11y APK installed) — it runs in the Android E2E workflow, never the "
        "fast gate",
        allow_module_level=True,
    )
# Resolve "booted" to the one running emulator now (the module only reaches here on an on-device
# host); a concrete serial passes through. Both the driver and the reseed launch validate it.
SERIAL: str = adb.resolve_serial(_serial)

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-compose"  # the Compose a11y app: its testTag ids surface as adb resource-ids


def _wire(ids: list[str]) -> str:
    """Encode an identifier spec for the SHOWCASE_CONFORMANCE extra (see AppModel.parseConformance).

    A leading `,` sentinel keeps the value non-empty even for the empty (zero-match) screen: adb's
    `am start --es KEY ""` drops an empty trailing arg, so a bare empty string would reach the app as
    "not in conformance mode" rather than the empty set.
    """
    return "," + ",".join(ids)


# Boot the fixture straight into (the empty) conformance mode, not the normal tab app: the app enters
# ConformanceScreen and renders just the marker element, which the launch readiness probe snapshots.
# SHOWCASE_UITEST (also supplied by the target's launchEnv) is set explicitly for clarity.
_CONFORMANCE_ENV = {"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": _wire([])}


class _AndroidHarness(OnDeviceConformanceHarness):
    """Realizes each conformance screen by re-launching the activity with a new SHOWCASE_CONFORMANCE.

    The shared `with_screen` / condition-backed `_await_screen` live in the base
    (`OnDeviceConformanceHarness`); this backend supplies only `_realize` — re-launching the
    `singleTask` activity with the identifier spec as an intent extra, which `onNewIntent` hands to
    the model so ConformanceScreen re-renders. The adb analogue of iOS's spec-file write.
    """

    def __init__(self, driver: base.Driver, serial: str, component: str) -> None:
        super().__init__("adb", driver)
        self._serial = serial
        self._component = component

    def _realize(self, ids: list[str]) -> None:
        # `am start -n <component> --es SHOWCASE_CONFORMANCE <wire>` delivers the spec to the running
        # activity via onNewIntent (BE-0270) — no relaunch, no file inside the app sandbox.
        adb._real_run(
            adb.launch_cmd(self._serial, self._component, {"SHOWCASE_CONFORMANCE": _wire(ids)})
        )


@pytest.fixture(scope="module")
def _eff() -> Effective:
    # A raw resolve() bypasses `_load_effective_with_source`, so the config's relative appPath would
    # stay config-relative and miss where it points from here. Rebase against the config's own
    # directory (unconfined, like a local config, BE-0242) so launch_driver sees the same absolute
    # appPath the CLI would — mirrors the iOS on-device module.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def _adb_driver(_eff: Effective) -> base.Driver:
    driver, _readiness = launch_driver(SERIAL, _eff, "adb", extra_env=_CONFORMANCE_ENV)
    return driver


@pytest.fixture(scope="module")
def _component(_eff: Effective) -> str:
    """The launcher component (`<package>/<activity>`) the reseed re-launches, resolved once."""
    return adb.Env(SERIAL).resolve_activity(require_android(_eff).package)


class TestAdbDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, _adb_driver: base.Driver, _component: str) -> ConformanceHarness:
        return _AndroidHarness(_adb_driver, SERIAL, _component)
