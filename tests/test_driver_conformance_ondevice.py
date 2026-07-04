"""Run the driver conformance contract (BE-0114) against the on-device backends (idb + XCUITest).

Unlike the FakeDriver suite (browser-free, on the fast Linux gate) and the Playwright suite (web
CI), this drives the real iOS Simulator backends: idb via idb_companion, XCUITest via the resident
BajutsuRunner. The point of the suite is to catch drift on a backend's *own* query / act code,
which only surfaces against the real actuator — so it needs a booted Simulator with the showcase
a11y app installed (and, for XCUITest, the built runner). It runs in the on-device E2E path
(`e2e.yml`), never in `make check`: an `ondevice` pytest marker (deselected by the gate's default
`-m 'not web and not ondevice'`) keeps it out even when the idb extra is installed, and a
module-level skip drops it whenever `BAJUTSU_CONFORMANCE_UDID` is unset — the fast gate's state.

Each conformance screen is realized on-device by relaunching the showcase app with the
`SHOWCASE_CONFORMANCE` launch env (BE-0114): a comma-separated identifier spec renders a flat
screen of exactly those accessibility identifiers — duplicates, the empty set, unique — so the
same contract that seeds FakeDriver's `screen=` and Playwright's HTML drives the real device. A
launch env (not a runtime deeplink) is used deliberately: `simctl openurl` for a custom scheme
raises iOS's "Open in app?" confirmation, which would block the screen behind a system dialog.

Run serially (`-n0`): the suite reseeds one shared Simulator by relaunching the app, so parallel
xdist workers would clobber each other's screen. The `e2e.yml` job passes `-n0`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from driver_conformance import ConformanceHarness, DriverConformanceContract

from bajutsu import simctl
from bajutsu.config import Effective, load_config, resolve
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
_TARGET = "showcase-swiftui"  # the a11y app: its identifiers surface for both idb and XCUITest


class _OnDeviceHarness:
    """Realizes each conformance screen by relaunching the app with `SHOWCASE_CONFORMANCE`.

    Each `with_screen` relaunches (simctl `--terminate-running-process`) with the identifier spec in
    the launch env, then waits until the driver's own `query()` reflects the new screen — the
    on-device analogue of FakeDriver's `screen=` and Playwright's `set_content`. The driver is
    stateless per query, so the same instance is reused across relaunches.
    """

    def __init__(
        self, backend: str, driver: base.Driver, bundle_id: str, run: simctl.RunFn
    ) -> None:
        self.backend = backend
        self._driver = driver
        self._bundle_id = bundle_id
        self._run = run

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        ids = [el["identifier"] for el in elements if el["identifier"] is not None]
        env = simctl.child_env({"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": ",".join(ids)})
        self._run(simctl.launch_cmd(UDID, self._bundle_id), env)
        self._await_screen(ids)
        return self._driver

    def _await_screen(self, ids: list[str], timeout: float = 15.0, poll: float = 0.1) -> None:
        # Condition-backed (no fixed sleep): a relaunch settles asynchronously, so wait on the
        # observed screen — every requested id present, or (the empty screen) the app back up — not
        # on a guessed delay.
        deadline = time.monotonic() + timeout
        while True:
            present = {el["identifier"] for el in self._driver.query()}
            ready = all(i in present for i in ids) if ids else bool(present)
            if ready:
                return
            if time.monotonic() >= deadline:
                seen = sorted(p for p in present if p)
                raise AssertionError(f"conformance screen not ready: want {ids}, saw {seen}")
            time.sleep(poll)


def _effective() -> Effective:
    return resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)


@pytest.fixture(scope="module")
def _eff() -> Effective:
    return _effective()


@pytest.fixture(scope="module")
def _idb_driver(_eff: Effective) -> base.Driver:
    return launch_driver(UDID, _eff, "idb")


@pytest.fixture(scope="module")
def _xcuitest_driver(_eff: Effective) -> base.Driver:
    return launch_driver(UDID, _eff, "xcuitest")


class TestIdbDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, _eff: Effective, _idb_driver: base.Driver) -> ConformanceHarness:
        return _OnDeviceHarness("idb", _idb_driver, _eff.bundle_id, simctl._real_run)


class TestXcuitestDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, _eff: Effective, _xcuitest_driver: base.Driver) -> ConformanceHarness:
        return _OnDeviceHarness("xcuitest", _xcuitest_driver, _eff.bundle_id, simctl._real_run)
