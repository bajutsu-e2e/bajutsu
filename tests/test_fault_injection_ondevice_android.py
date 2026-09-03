"""Inject a real degenerate accessibility read and watch the adb retry survive it (BE-0305).

`CoordinateTreeDriver`'s transient-empty retry exists because a real device intermittently serves an
almost-empty accessibility tree while a screen is in flux, and a selector resolved against that tree
would fail with a false "element not found". Its unit test feeds the loop a fabricated count sequence
(`[3, 1, 3]`) with the backoff zeroed. That proves the control flow, and nothing about whether the
real device's degenerate response actually trips the detection heuristic: `_is_transient_empty` keys on
an element *count* below a floor, so a regression to that floor — or a read source that returns an
error rather than a small tree — would leave the fabricated fixture green and ship a broken retry.

This lane injects the real condition. Putting the device to sleep makes the real read source (the
resident UI Automator channel, and the `uiautomator dump` fallback behind it) return a genuinely empty
tree — measured at 0 elements against the floor of 2, with no error to mistake it for something else —
and waking it restores the real screen. The display is put down on the state the platform itself
reports and brought back on the driver's own retry record, never on a fixed delay, so a case does not
depend on guessing how long a read takes. Two complementary contracts:

* a degenerate read that clears is ridden out — the resolve that follows sees the real screen;
* a degenerate read that never clears fails loudly, rather than a selector quietly resolving against
  an empty tree or the driver hanging on it.

Runs in the Android E2E path (`android-e2e.yml`), never in `make check`: the `ondevice` marker is
deselected by the gate's default `-m 'not web and not ondevice'` even where adb is on PATH, and a
module-level skip drops it whenever `BAJUTSU_FAULT_INJECTION_SERIAL` is unset — the fast gate's state.

Scoped to the Compose showcase (like the adb conformance lane) and its launch screen, whose
`stable.row.1` is the same element the shared smoke scenario waits for on every target, so the case
reads back an element already proven to be there.

Run serially (`-n0`): the cases put one shared device to sleep, so parallel workers would inject
faults into each other's reads. The `android-e2e.yml` job passes `-n0`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import fault_injection
import ondevice_evidence
import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals
from bajutsu.common.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

# The E2E workflow provisions a booted emulator with the showcase app and signals it here; absent (any
# box without a device, the fast gate), skip the whole module. The `ondevice` marker also deselects it,
# so this is belt-and-braces. A knob of its own rather than the conformance lane's, so the two jobs are
# enabled independently.
_serial = os.environ.get("BAJUTSU_FAULT_INJECTION_SERIAL")
if not _serial:
    pytest.skip(
        "adb fault injection needs BAJUTSU_FAULT_INJECTION_SERIAL (a booted emulator/device with the "
        "showcase Compose a11y APK installed) — it runs in the Android E2E workflow, never the fast gate",
        allow_module_level=True,
    )
# Resolve "booted" to the one running emulator now (the module only reaches here on an on-device host);
# a concrete serial passes through, and the driver validates it again.
SERIAL: str = adb.resolve_serial(_serial)

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-compose"  # the Compose a11y app: its testTag ids surface as adb resource-ids

# The launch screen's first catalog row: present on every target and already the element the shared
# smoke scenario waits for, so a case that cannot resolve it after the fault has found a real problem.
_KNOWN: base.Selector = {"id": "stable.row.1"}

_TREE_LOGGER = "bajutsu.common.drivers.coordinate_tree"
# Fragments of the read path's own log records — what the cases lift the fault on and assert against,
# so a reworded record fails loudly rather than silently weakening the lane.
_RETRYING = "a transient empty; retrying"
_EXHAUSTED = "returning the degenerate tree"

# `input keyevent` codes for the display. Both are idempotent, unlike toggling POWER (26), so a case
# that runs against an already-sleeping or already-awake device still reaches the state it wants.
_KEYCODE_SLEEP = 223
_KEYCODE_WAKEUP = 224

# Bounds the wait for the retry record and for the display to reach a requested state. Long enough for
# a loaded CI emulator to work through the retry budget on the slow (`uiautomator dump`, ~2s a read)
# path, short enough that a state that never arrives fails the case instead of hanging the job.
_TRIGGER_TIMEOUT = 120.0
_WAKEFULNESS_TIMEOUT = 30.0


@pytest.fixture(scope="module")
def _eff() -> Effective:
    # A raw resolve() leaves the config's relative appPath config-relative; rebase against the config's
    # own directory (unconfined, like a local config, BE-0242) so launch_driver sees the same absolute
    # appPath the CLI would — mirrors the on-device conformance modules.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def _adb_driver(_eff: Effective) -> base.Driver:
    driver, _readiness = launch_driver(SERIAL, _eff, "adb", extra_env={"SHOWCASE_UITEST": "1"})
    return driver


@pytest.fixture(autouse=True)
def _evidence(request: pytest.FixtureRequest) -> Iterator[None]:
    """Video + deviceLog for this case, kept only on failure (the CI job otherwise has neither)."""
    yield from ondevice_evidence.capture(
        SERIAL,
        "fault-injection-adb",
        request,
        start_video=ondevice_evidence.android_screenrecord,
        start_log=intervals.start_logcat,
    )


@pytest.fixture
def driver(_adb_driver: base.Driver) -> Iterator[base.Driver]:
    """The lease, guaranteed awake and showing the app before and after every case."""
    _wake()
    _await_wakefulness("Awake")
    yield _adb_driver
    # A case that failed mid-fault must not leave the next one (or the rest of the lane, or a developer's
    # device) staring at a dark screen.
    _wake()
    _await_wakefulness("Awake")


def _wakefulness() -> str:
    """The platform's own display state (`Awake` / `Asleep`) — what the cases wait on.

    Raises:
        AssertionError: if the state cannot be read at all. A platform that renamed the field would
            otherwise make every wait time out reading "the display would not sleep", when the truth
            is that the lane can no longer see the display state.
    """
    out = adb.real_run(adb._adb(SERIAL, "shell", "dumpsys", "power"))
    for line in out.splitlines():
        if "mWakefulness=" in line:
            return line.split("mWakefulness=", 1)[1].split()[0]
    raise AssertionError(
        "`dumpsys power` reported no mWakefulness field, so the display state is unreadable and the "
        f"fault cannot be confirmed either way; output was:\n{out}"
    )


def _await_wakefulness(want: str, timeout: float = _WAKEFULNESS_TIMEOUT, poll: float = 0.1) -> None:
    # Condition-backed (no fixed sleep): the display settles asynchronously after the key event, so
    # wait on the state the platform reports rather than a guessed delay. A state that never arrives
    # fails loudly — a case that read the tree before the fault landed would prove nothing.
    deadline = time.monotonic() + timeout
    while True:
        seen = _wakefulness()
        if seen == want:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"display never reached {want} within {timeout}s (saw {seen})")
        time.sleep(poll)


def _sleep_display() -> None:
    """Inject the fault: with the display down, the real read source serves an empty tree."""
    adb.real_run(adb.keyevent_cmd(SERIAL, _KEYCODE_SLEEP))
    _await_wakefulness("Asleep")  # confirm the fault landed before any case reads the tree


def _wake() -> None:
    """Lift the fault: wake the display and dismiss any keyguard, so the app's tree is readable again.

    One device-shell round trip, and no state confirmation, because a lift races a bounded retry: five
    reads and 0.75s of backoff, about 1.2s on the resident channel. A second `adb` invocation, or a
    `dumpsys` poll confirming what the key event has already done, would spend that budget rather than
    the display coming back. `dismiss-keyguard` is harmless where nothing is locked (the CI emulator)
    and needed on a device that shows a keyguard on wake, whose window would otherwise stand between
    the read and the app. A caller that needs the state settled — the fixture around each case — waits
    for it itself.
    """
    adb.real_run(
        adb._adb(SERIAL, "shell", f"input keyevent {_KEYCODE_WAKEUP}; wm dismiss-keyguard")
    )


def test_a_real_degenerate_read_is_ridden_out_by_the_transient_retry(driver: base.Driver) -> None:
    # The retry against the real condition: the pre-fault read leaves a rich tree on record (which is
    # what arms the heuristic — a screen that has only ever been sparse is taken at face value), the
    # display goes down so the device really serves an empty tree, and the display comes back the
    # moment the driver reports it is retrying. The read then resolves the known element, so the real
    # degenerate response never surfaced as a false "element not found".
    base.resolve_unique(driver.query(), _KNOWN)
    with fault_injection.watch(_TREE_LOGGER, _RETRYING) as log:
        _sleep_display()
        with fault_injection.lifted_when_reached(log, _wake, timeout=_TRIGGER_TIMEOUT):
            elements = driver.query()
    assert log.mentions(_RETRYING), (
        f"the real empty read never tripped the transient-empty retry:\n{log.report()}"
    )
    # The retry budget is bounded, so a lift is racing it. Separate losing that race from the failure
    # this case exists to catch: without this, a display that came back a beat too late would fail on
    # the resolve below with a bare "element not found" — indistinguishable from a broken retry.
    assert not log.mentions(_EXHAUSTED), (
        "the display did not come back inside the retry budget, so this run says nothing about the "
        f"retry itself (the lane is racing the lift; widen the budget or speed the lift):\n{log.report()}"
    )
    base.resolve_unique(elements, _KNOWN)


def test_a_degenerate_read_that_never_clears_fails_loudly(driver: base.Driver) -> None:
    # The complement, and the reason the retry is bounded: an empty tree that stays empty must end the
    # step with a loud, specific failure — never a silent success, and never an unbounded wait. Held
    # asleep for the whole resolve, the driver spends its retry budget, reports it, and raises
    # `ElementNotFound`.
    base.resolve_unique(driver.query(), _KNOWN)
    with fault_injection.watch(_TREE_LOGGER, _EXHAUSTED) as log:
        _sleep_display()
        with pytest.raises(base.ElementNotFound):
            driver.tap(_KNOWN)
    assert log.mentions(_EXHAUSTED), (
        f"the retry budget ran out without reporting it:\n{log.report()}"
    )
    # The case exists to prove the retry is *bounded*, which a retry that never happened would satisfy
    # just as well: an empty tree with the retries disabled reports the same exhaustion.
    assert log.mentions(_RETRYING), (
        f"the budget was reported spent without a single retry having been attempted:\n{log.report()}"
    )
