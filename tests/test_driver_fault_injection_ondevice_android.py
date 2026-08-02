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

**Reads must go over `uiautomator dump`, not the resident channel.** That is the whole premise, and
it is the opposite of what this suite first assumed. The transient-empty tree is a *stock*
`uiautomator dump` artifact — the "null root node" text above is what the platform's own dump returns
mid-transition. The resident UI Automator server (BE-0245) exists partly to eliminate exactly that
class of artifact, and BE-0332 Unit 4 finished the job: `respondSource` runs `waitForIdle()` and then
`settledDump()` — re-dumping until two consecutive dumps are byte-identical — *before* it answers,
unconditionally, whether or not a `since` mark rides on the request. So the device settles the
transient away on its own side, and a host reading through that channel can never observe one, at any
tap speed. Five CI runs against the resident channel reproduced nothing for that reason, not a timing
one — the last of them after the tap path had been made measurably denser, which moved the reads onto
more partial transitions and still changed no outcome. The module therefore forces the dump path
(`BAJUTSU_ADB_RESIDENT=0`, the documented knob) and asserts it got it.

Forcing it through the *environment* rather than blanking the driver's channel afterwards is what
keeps the fault honest. A live resident server holds the device's single UiAutomation session, so a
dump taken beside one reads empty for the rest of the lease (`adb_resident.py`) — a wedge, not a
transient, and one that would satisfy a naive "did the tree come back empty?" check while proving
nothing. With the knob the server is never started, so the empty trees seen here are the real
mid-transition artifact. The recovery assertion at the end of the test is the second guard: a wedge
never recovers, so it fails loudly instead of passing as a reproduction.

The fault is contention, the escape hatch the item allows where a naturally-timed transient cannot be
scheduled. A dump costs seconds to start, far longer than one tab transition, so a single tap can
never be raced — the contention is sustained instead: a background thread taps the tab bar
continuously while the main thread reads, so a dump starting at any moment still lands amid ongoing
transitions. The app is never modified and no read is faked — the driver's own `_read_source` and
`parse_hierarchy` produce the empty tree the retry then rides over. The taps go over one `adb shell`
held open for the whole test, not a fresh process each time, so the tapping is dense enough to keep
the screen genuinely unsettled rather than paying an adb handshake between taps.

It stops the instant the fault lands, though — the tapper watches the retry counter and returns as
soon as it moves, even mid-`query`. The retry under test is what runs *after* the first empty read,
and it exists to ride over a transition that is *ending*, so it has to see the screen calming, as it
would on a device left alone. Tapping through those attempts denies the mechanism the only condition
it can recover in, and the never-recovering empty that follows reads as a wedged accessibility bridge
when nothing is wedged (observed: a run whose retry fired 15 times and never came back, against 6 and
a clean recovery when it was left alone).

Contention cannot be scheduled, so the loop is bounded by a wall-clock deadline and its failure is
loud: a run that never reproduces the condition fails with that as the diagnosis rather than passing
on an untested mechanism. That inherent flakiness is why the lane lands as a non-gating signal first
(`fault (adb)` in `android-e2e.yml`), following BE-0282's precedent, and is promoted once stable.

Runs in the Android E2E path, never in `make check`: the `ondevice` marker is deselected by the
gate's default, and a module-level skip drops it whenever `BAJUTSU_FAULT_SERIAL` is unset.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
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

# How long to keep the screen contended while reading, before giving up on reproducing the condition.
# A wall-clock bound, not a round count: a `uiautomator dump` read costs seconds and varies with load,
# so a fixed round count would be minutes on a slow emulator and seconds on a fast one. Sized to give
# an emulator that produces the transient at all many dumps' worth of chances while leaving the job
# far inside its timeout.
_CONTENTION_BUDGET_S = 90.0

# Gap between the background thread's taps. Short enough to keep transitions overlapping (a tab
# transition is a few hundred ms), long enough that each tap still registers as its own gesture.
_TAP_INTERVAL_S = 0.15

# How long the screen has to come back after the contention stops, before an empty is called a wedge
# rather than the transient this suite injects. Sized well above `query`'s own retry budget (~0.75s):
# that budget answers "is this read a mid-transition blip", while this answers the different question
# "did the bridge survive being hammered", which it can legitimately take seconds to do.
_RECOVERY_BUDGET_S = 30.0


@pytest.fixture(scope="module")
def _eff() -> Effective:
    # Rebase against the config's own directory so the relative appPath resolves from here the way
    # the CLI would (unconfined, like a local config, BE-0242) — mirrors the on-device conformance
    # modules.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def driver(_eff: Effective) -> Iterator[AdbDriver]:
    # Force the dump read path for this lease. Set before `launch_driver` so the environment never
    # starts the resident server at all: blanking the driver's channel afterwards would leave the
    # server holding the device's single UiAutomation session, and every dump beside it reads empty
    # for the rest of the lease — a wedge that would look like the transient this suite hunts.
    mp = pytest.MonkeyPatch()
    mp.setenv("BAJUTSU_ADB_RESIDENT", "0")
    try:
        driver, _readiness = launch_driver(SERIAL, _eff, "adb", extra_env={"SHOWCASE_UITEST": "1"})
        assert isinstance(driver, AdbDriver), (
            f"the adb backend resolved to {type(driver).__name__}, which does not carry the "
            "transient-empty retry this suite injects a fault for"
        )
        # The dump path is the premise of the whole suite: the resident channel settles the transient
        # away on the device before answering (BE-0332 Unit 4's `settledDump`), so a read through it
        # can never observe one. Reaching here on the resident channel would mean the knob above
        # stopped being honoured, which must fail as itself rather than as "the emulator never
        # reproduced the condition".
        assert driver._fetch_hierarchy is None, (
            "reads are going over the resident UI Automator channel despite BAJUTSU_ADB_RESIDENT=0; "
            "that channel runs `waitForIdle` + `settledDump` before it answers, so the transient "
            "this suite injects is settled away on the device and can never be observed"
        )
        yield driver
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def tap_shell(driver: AdbDriver) -> Iterator[subprocess.Popen[str]]:
    """A single persistent `adb shell`, so a tap costs one stdin write, not a fresh adb process.

    A fresh `adb shell input tap` pays a full adb client-server handshake every time; on a loaded CI
    emulator that alone can run over a second, which would leave the "sustained" contention this
    suite depends on full of second-wide gaps the screen settles in. One persistent shell removes it:
    the device executes each `input tap` the instant its line reaches the shell's stdin.

    This is about contention *density*, not about why the lane took six runs to reach the branch —
    that was the read channel (see the module docstring), and no tap timing could have fixed it. The
    handshake was measurably real, though: removing it took a run from 5 distinct screens seen across
    the loop to 9, so the reads did start landing amid more partial transitions. It just could not
    matter while the resident server settled every one of them away before answering.
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
        # Best-effort: this fixture tears down after a test that deliberately hammers the device, so a
        # wedged shell is plausible here. Letting teardown raise would replace the real diagnosis with
        # a cleanup error, so escalate to kill and give up rather than propagate.
        with contextlib.suppress(OSError):
            proc.stdin.close()
            proc.terminate()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
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
    # Distinct screens seen while reading. A tapper that stops landing its taps (Compose can drop one
    # aimed at a view mid-transition) would end the contention silently and spend the budget looking
    # like an emulator that never produces the transient, so the count goes into the failure below.
    seen = {driver._stable_key(tree)}
    reads = 0

    stop = threading.Event()

    def tapper() -> None:
        cycle = 0
        while not stop.is_set():
            # Stop the instant the fault has been injected, mid-`query` if that is when it lands.
            # The retry under test is the thing that runs *after* the first empty read, and it is
            # meant to ride over a transition that is ending — so it has to see the screen calming,
            # exactly as it would on a device left alone. Tapping through those attempts instead
            # denies the mechanism the only condition it can recover in, and the resulting
            # never-recovering empty reads as a wedged bridge when nothing is wedged at all.
            if driver._transient_empty_retries > before:
                return
            x, y = taps[cycle % len(taps)]
            _tap(tap_shell, x, y)
            cycle += 1
            stop.wait(_TAP_INTERVAL_S)  # a cancellable sleep: `stop` ends the wait immediately

    thread = threading.Thread(target=tapper, name="fault-adb-tapper", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + _CONTENTION_BUDGET_S
        while time.monotonic() < deadline:
            # No readiness wait, and no assertion on this tree: under contention a read legitimately
            # lands on a torn or degenerate screen. Whether the retry *recovers* is settled below,
            # on the quiet screen — asserting it here would fail the run for the contention it was
            # asked to create. The tapper stops itself the moment the first empty read lands, so the
            # retry attempts inside this very `query` already run against a calming screen.
            tree = driver.query()
            reads += 1
            seen.add(driver._stable_key(tree))
            if driver._transient_empty_retries > before:
                break
    finally:
        stop.set()
        thread.join(timeout=5)

    if driver._transient_empty_retries == before:
        pytest.fail(
            f"{reads} contended `uiautomator dump` reads over {len(seen)} distinct screens never "
            f"produced a transient-empty tree in {_CONTENTION_BUDGET_S}s, so the retry this suite "
            "exists to exercise was never reached. Either the taps stopped switching tabs (a low "
            "screen count says so), the emulator no longer returns an unparseable hierarchy while "
            "the screen is in flux, or the read path no longer reports one as an empty element list"
        )

    # The retry fired; now prove the empty was *transient* — that the screen comes back once the
    # contention stops — rather than a wedged accessibility bridge, which must never pass as a
    # reproduction. Polled rather than read once: `query`'s own retry budget (~0.75s) is sized for a
    # mid-transition blip, not for the bridge catching its breath after this suite deliberately
    # hammered it, so a single read here would fail a device that does recover a moment later. A
    # bounded condition wait, never a fixed sleep — it returns the instant the screen resolves, and a
    # genuine wedge still fails loudly when the window expires.
    recovery_deadline = time.monotonic() + _RECOVERY_BUDGET_S
    while True:
        settled = driver.query()
        try:
            base.resolve_unique(settled, _ALWAYS_PRESENT)
            break
        except base.SelectorError as exc:
            if time.monotonic() >= recovery_deadline:
                pytest.fail(
                    f"the retry fired ({driver._transient_empty_retries - before} re-reads) but the "
                    f"screen never came back within {_RECOVERY_BUDGET_S}s of the contention "
                    f"stopping: the last read returned {len(settled)} elements with no unique "
                    f"{_ALWAYS_PRESENT}. An empty that outlives the contention is a wedged "
                    f"accessibility bridge, not the transient this suite injects ({exc})"
                )
