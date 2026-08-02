"""Drive the adb transient-empty retry with a real device fault, not a fabricated count sequence (BE-0305).

`CoordinateTreeDriver`'s transient-empty retry exists for one condition: UI Automator answering a
read with no parseable hierarchy, which `slice_hierarchy_root` degrades to an empty element list.
Mid-transition that is the "null root node returned by UiTestAutomationBridge" text; a truncated or
garbled response reaches the retry the same way. Every existing check of that retry fabricates the
condition. `tests/test_coordinate_tree.py` feeds the driver a scripted
`[3, 1, 3]` element-count sequence with the backoff zeroed, which proves the control flow runs but
says nothing about whether `_is_transient_empty`'s threshold still fires on what the device really
returns; the on-device conformance suite never reaches the branch at all, because
`OnDeviceConformanceHarness._await_screen` waits every screen to readiness before the contract reads
it. This module closes that gap by making the emulator produce the condition for real.

The fault is contention, the escape hatch the item allows where a naturally-timed transient cannot be
scheduled: a real tab tap fires and the tree is read immediately, with no readiness wait in between,
so some reads land while UI Automator has no stable window to describe. The app is never modified and
no read is faked — the driver's own `_read_source` and `parse_hierarchy` produce the empty tree the
retry then rides over. Reads go over the resident UI Automator channel (BE-0245): at roughly 0.1 s a
read lands inside a transition often enough to reproduce the condition, which the ~2.4 s `uiautomator
dump` startup would almost always outlast.

The tap is a raw `input tap` rather than through the driver's own actuator, because that actuator
settles the screen before returning — and a settled screen is exactly the state in which the
transient can no longer be observed. It runs over one `adb shell` opened once and held for the whole
loop, not a fresh `adb shell input tap` process per round: a fresh process pays a full adb
client-server handshake every round, which on a loaded CI emulator can run past a second — longer
than the tab transition itself, and enough on its own to explain a loop that completes every round
without ever landing inside one.

Contention cannot be scheduled, so the loop is bounded and its failure is loud: a round that never
reproduces the condition fails with that as the diagnosis rather than passing on an untested
mechanism. That inherent flakiness is why the lane lands as a non-gating signal first
(`fault (adb)` in `android-e2e.yml`), following BE-0282's precedent, and is promoted once stable.

Runs in the Android E2E path, never in `make check`: the `ondevice` marker is deselected by the
gate's default, and a module-level skip drops it whenever `BAJUTSU_FAULT_SERIAL` is unset.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from bajutsu import adb
from bajutsu.config import Effective, load_config, resolve
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver
from bajutsu.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

# The E2E workflow provisions a booted emulator with the showcase app and signals it here; absent
# (any box without a device, the fast gate), skip the whole module. The `ondevice` marker also
# deselects it, so this is belt-and-braces — the suite never runs, or errors, off an on-device host.
_serial = os.environ.get("BAJUTSU_FAULT_SERIAL")
if not _serial:
    pytest.skip(
        "on-device adb fault injection needs BAJUTSU_FAULT_SERIAL (a booted emulator/device with "
        "the showcase Compose a11y APK installed) — it runs in the Android E2E workflow, never the "
        "fast gate",
        allow_module_level=True,
    )
SERIAL: str = adb.resolve_serial(_serial)

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-compose"

# The bottom navigation bar renders on every tab, so this selector resolves uniquely whichever tab a
# read caught — a read missing it is a degenerate tree the retry failed to ride over, which is the
# false "element not found" this suite exists to rule out. Selected by label and trait, the form the
# shipped Android scenarios already use to drive this tab bar (BE-0223), not by id.
_ALWAYS_PRESENT: base.Selector = {"label": "Stable", "traits": ["button"]}

# Cycled so every round is a real switch to a different tab. Stable is left out of the cycle only to
# keep every round a change of screen; its bar item is still what `_ALWAYS_PRESENT` resolves.
_TABS = ("Search", "Log", "Notices", "Permissions")

# How many tap-and-read rounds to spend reproducing the condition. Each round costs one tap plus one
# resident read (~0.2 s together), so the ceiling is seconds, not minutes — high enough that an
# emulator producing the transient at all will show it, low enough to fail fast when none does.
_MAX_ROUNDS = 60


@pytest.fixture(scope="module")
def _eff() -> Effective:
    # Rebase against the config's own directory so the relative appPath resolves from here the way
    # the CLI would (unconfined, like a local config, BE-0242) — mirrors the on-device conformance
    # modules.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def driver(_eff: Effective) -> AdbDriver:
    driver, _readiness = launch_driver(SERIAL, _eff, "adb", extra_env={"SHOWCASE_UITEST": "1"})
    assert isinstance(driver, AdbDriver), (
        f"the adb backend resolved to {type(driver).__name__}, which does not carry the "
        "transient-empty retry this suite injects a fault for"
    )
    # The fast resident read is the premise of the whole suite, and a missing server APK or a failed
    # channel start degrades to `uiautomator dump` with nothing louder than a warning. Left
    # unchecked, that degrade would surface as "the emulator never reproduced the condition" —
    # blaming the device for a job that read through the one channel too slow to catch a transition.
    assert driver._fetch_hierarchy is not None, (
        "reads fell back to `uiautomator dump`: the resident UI Automator server (BE-0245) is not "
        "running, and its ~2.4 s read startup outlasts the transition this suite injects"
    )
    return driver


@pytest.fixture(scope="module")
def tap_shell(driver: AdbDriver) -> Iterator[subprocess.Popen[str]]:
    """A single persistent `adb shell`, so a tap costs one stdin write, not a fresh adb process.

    A fresh `adb shell input tap` per round pays a full adb client-server handshake every round; on
    a loaded CI emulator that alone can run over a second — longer than the tab transition this
    suite is trying to catch, and the actual reason four straight CI runs completed every round
    without ever landing inside one (a round's ~1.4 s badly outweighs a resident read's ~0.1 s, so
    the handshake, not the read, was eating the window). One persistent shell removes it: the
    device executes each `input tap` the instant its line reaches the shell's stdin.
    """
    proc = subprocess.Popen(
        adb._adb(SERIAL, "shell"),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert proc.stdin is not None, "Popen with stdin=PIPE always attaches a stdin stream"
    try:
        yield proc
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)


def _tap(shell: subprocess.Popen[str], x: float, y: float) -> None:
    """Fire a raw `input tap` over the persistent shell — no settle, no fresh adb process."""
    assert shell.stdin is not None
    shell.stdin.write(f"input tap {round(x)} {round(y)}\n")
    shell.stdin.flush()


def _center(el: base.Element) -> tuple[float, float]:
    """The frame centre to aim a raw `input tap` at, the same point the driver's own tap uses."""
    x, y, w, h = el["frame"]
    return x + w / 2, y + h / 2


def test_the_retry_rides_over_a_real_mid_transition_empty_tree(
    driver: AdbDriver, tap_shell: subprocess.Popen[str]
) -> None:
    tree = driver.query()  # baseline: a full screen, so `_is_transient_empty` is armed here
    taps = [
        _center(base.resolve_unique(tree, {"label": tab, "traits": ["button"]})) for tab in _TABS
    ]
    before = driver._transient_empty_retries
    # Distinct screens seen across the loop. A tap that stops landing (Compose can drop one aimed at
    # a view mid-transition) would end the contention silently and exhaust the rounds looking like an
    # emulator that never produces the transient, so the count goes into the failure below.
    seen = {driver._stable_key(tree)}

    for round_index in range(_MAX_ROUNDS):
        x, y = taps[round_index % len(taps)]
        _tap(tap_shell, x, y)
        # No readiness wait: reading straight after the tap is what puts the read inside the
        # transition. A selector failure here is the failure this suite is for, so it is reported
        # with the round it happened on rather than as a bare "element not found" on the device.
        tree = driver.query()
        seen.add(driver._stable_key(tree))
        try:
            base.resolve_unique(tree, _ALWAYS_PRESENT)
        except base.SelectorError as exc:
            pytest.fail(
                f"round {round_index}: the settled read returned {len(tree)} elements with no unique "
                f"{_ALWAYS_PRESENT}. The retry either never fired, or fired and gave up before the "
                f"screen came back; a partial tree of two or more nodes never enters the retry at "
                f"all, since `_READY_MIN` treats it as settled ({exc})"
            )
        if driver._transient_empty_retries > before:
            return

    pytest.fail(
        f"{_MAX_ROUNDS} tap-and-read rounds over {len(seen)} distinct screens never produced a "
        "transient-empty tree, so the retry this suite exists to exercise was never reached. Either "
        "the taps stopped switching tabs (a low screen count says so), the emulator no longer "
        "produces the transient at this read speed, or the read path no longer reports an "
        "unparseable hierarchy as an empty element list"
    )
