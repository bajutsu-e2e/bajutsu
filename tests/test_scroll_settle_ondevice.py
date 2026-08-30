"""The non-inertial `scroll` contract, checked as motion rather than as overlap (BE-0326, BE-0329).

BE-0329's conformance case states the contract in the only terms an element tree can: two reads
either share an unclipped element or they do not. That is a proxy, and on iOS the tree cannot be
anything better — XCUITest's snapshot waits for the app to quiesce, so the first read after a gesture
already outlasts any deceleration and reports a settled screen whether or not one ever occurred.
Measured: sampling `driver.query()` every ~14ms for 2.5s after the call returned reported a 0.0 px
shift for `scroll` *and* for the deliberately inertial `swipe`.

So this asserts the property directly, off the rendered screen. `simctl io screenshot` reads the
framebuffer out of band, whatever the app is doing, so a background sampler can show whether content
was still moving at the moment `/scroll` returned to the driver — the thing the re-query loop relies
on being false.

Kept out of `test_driver_conformance_ondevice.py` deliberately: that module is the backend-neutral
contract, realized per backend, and this instrument is not neutral — it needs a host-side framebuffer
grab that only the Simulator offers.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from backend_crash_recovery import LeaseHolder, record_absorbed_stall
from ondevice_spec_path import SpecPathMemo, read_data_container
from PIL import Image, ImageChops
from test_driver_conformance_ondevice import _OnDeviceHarness
from xcuitest_lease import xcuitest_lease_launch

from bajutsu import simctl
from bajutsu.config import Effective, ios_bundle_id, load_config, resolve

# The loop's own step geometry, from where it is defined — so this case scrolls exactly as the
# `scroll` action does rather than re-deriving endpoints that could drift from it.
from bajutsu.orchestrator.actions.handlers.scroll import (
    _STEP_FRACTION,
    _step_endpoints,
    _viewport,
)

# No `backend_crash_recovery` mark: this module holds one case, and a re-run would re-pay a lease
# rather than a retry the suite can absorb. A runner crash here is a real failure to look at.
pytestmark = [pytest.mark.ondevice]

_udid = os.environ.get("BAJUTSU_CONFORMANCE_UDID")
if not _udid:
    pytest.skip(
        "the settle check needs BAJUTSU_CONFORMANCE_UDID (a booted Simulator with the showcase app "
        "installed) — it runs on-device, never in the fast gate",
        allow_module_level=True,
    )
UDID: str = _udid

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-swiftui"
_CONFORMANCE_ENV = {"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": ""}

#: How long to keep sampling after the driver returns. Long enough for several frames at the
#: ~170ms/frame `simctl` cadence measured on an M-series host, and for the deceleration a regression
#: would leak (a `UIScrollView` fling runs well under a second), without paying for the ~900ms the
#: scroll indicator takes to fade — which the crop below ignores anyway.
_TAIL_S = 1.5

#: The trailing edge strip to ignore: the scroll indicator lives there and keeps animating after the
#: content has stopped, which is motion the contract says nothing about. Measured on a 1206px-wide
#: capture, the indicator occupied x=1188..1196 — the last 1.5% — so 4% is generous headroom for a
#: different indicator inset, while the content itself began at x=183 (15%) and cannot be swallowed.
_EDGE_IGNORE = 0.04

#: Per-pixel intensity change (of 255) that counts as a difference. The no-gesture control measured a
#: zero noise floor at this threshold across 55 frames, so this is not masking anything real; it is
#: headroom for another host's renderer dithering. Real residual motion is nowhere near it — the
#: smallest step this screen can realize moves content ~269pt (~800px), a wholesale redraw.
_PIXEL_DELTA = 12

#: The share of compared pixels allowed to differ. Sits between two measured numbers rather than
#: being guessed: the no-gesture control moved 0.0% of pixels, and a deliberately broken runner —
#: one returning before the gesture settled — moved 8.2% to 15.0% across its post-return frames. So it
#: clears the noise floor outright while still catching that regression by ~80x. (Residual motion
#: does not redraw the whole screen: this content is a list of similar rows, so a shifted list
#: overlaps itself over much of its area.)
_MOVED_TOLERANCE = 0.001

#: The check is worthless if the sampler never ran or the gesture never moved anything, so both are
#: asserted rather than assumed — an on-device fixture that passes vacuously proves nothing.
_MIN_POST_RETURN_FRAMES = 3


def _effective() -> Effective:
    # As in the conformance module: rebase the raw resolve against the config's own directory so the
    # relative appPath / testRunner point where they would from the CLI.
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


class _Sampler(threading.Thread):
    """Grab the framebuffer as fast as `simctl` allows, timestamping when each grab began.

    The timestamp is the subprocess's start, which is the conservative end: `simctl` samples the
    screen at some instant *after* that, so a frame recorded at or after the driver returned cannot
    show anything from before it.
    """

    def __init__(self, out: Path) -> None:
        super().__init__(daemon=True)
        self._out = out
        self._stop = threading.Event()
        self.frames: list[tuple[float, Path]] = []

    def run(self) -> None:
        i = 0
        while not self._stop.is_set():
            path = self._out / f"{i:04d}.png"
            started = time.monotonic()
            subprocess.run(
                ["xcrun", "simctl", "io", UDID, "screenshot", "--type=png", str(path)],
                check=True,
                capture_output=True,
            )
            self.frames.append((started, path))
            i += 1

    def stop(self) -> None:
        self._stop.set()
        self.join()


def _moved_fraction(a_path: Path, b_path: Path) -> float:
    """The share of pixels differing between two captures, ignoring the scroll-indicator strip."""
    a = Image.open(a_path).convert("L")
    b = Image.open(b_path).convert("L")
    width, height = a.size
    keep = int(width * (1.0 - _EDGE_IGNORE))
    box = (0, 0, keep, height)
    mask = ImageChops.difference(a.crop(box), b.crop(box)).point(
        lambda v: 255 if v > _PIXEL_DELTA else 0
    )
    return mask.histogram()[255] / float(keep * height)


@pytest.fixture(scope="module")
def _eff() -> Effective:
    return _effective()


@pytest.fixture(scope="module")
def _spec_paths(request: pytest.FixtureRequest, _eff: Effective) -> SpecPathMemo:
    node = request.node
    return SpecPathMemo(
        lambda: (
            Path(
                read_data_container(
                    UDID,
                    ios_bundle_id(_eff),
                    simctl.real_run,
                    lambda reason: record_absorbed_stall(node, reason),
                )
            )
            / "Documents"
            / "conformance-spec.txt"
        )
    )


@pytest.fixture(scope="module")
def _backend_launch(_eff: Effective) -> object:
    return xcuitest_lease_launch(UDID, _eff, extra_env=_CONFORMANCE_ENV)


@pytest.fixture
def harness(_backend_lease_holder: LeaseHolder, _spec_paths: SpecPathMemo) -> _OnDeviceHarness:
    # Read the driver off the holder rather than the launch thunk, as the conformance suite does, so
    # a lease replaced after a crash is the one this case drives.
    return _OnDeviceHarness(
        "xcuitest", _backend_lease_holder.driver, _spec_paths.for_lease(_backend_lease_holder)
    )


def test_scroll_leaves_the_content_at_rest_by_the_time_it_returns(
    harness: _OnDeviceHarness, tmp_path: Path
) -> None:
    driver = harness.scrollable_screen()

    elements = driver.query()
    frm, dest = _step_endpoints(elements, "down", None, _viewport(driver, elements), _STEP_FRACTION)

    out = tmp_path / "frames"
    out.mkdir()
    sampler = _Sampler(out)
    sampler.start()
    try:
        # A lead-in, so a pre-gesture frame exists to prove the step moved anything at all. Not a
        # synchronization wait: nothing is being waited *for*, the sampler is simply given room to
        # record the screen as it stood before the gesture.
        time.sleep(0.4)
        t_call = time.monotonic()
        driver.scroll(frm, dest)
        t_return = time.monotonic()
        time.sleep(_TAIL_S)
    finally:
        sampler.stop()

    settled = sampler.frames[-1][1]
    before = [p for t, p in sampler.frames if t < t_call]
    after = [(t, p) for t, p in sampler.frames if t >= t_return]

    assert before, "no pre-gesture frame was captured; the check would be vacuous"
    assert len(after) >= _MIN_POST_RETURN_FRAMES, (
        f"only {len(after)} frame(s) landed after the driver returned "
        f"(need {_MIN_POST_RETURN_FRAMES}); the check would be vacuous"
    )
    # The step must actually have moved the content, or a no-op gesture would satisfy everything
    # below by doing nothing.
    assert _moved_fraction(before[-1], settled) > _MOVED_TOLERANCE, (
        "the scroll step moved nothing, so this run proves nothing about settling"
    )

    still_moving = [
        (t - t_return, moved)
        for t, p in after
        if (moved := _moved_fraction(p, settled)) > _MOVED_TOLERANCE
    ]
    assert not still_moving, (
        "scroll returned while the content was still moving, so the re-query loop can read a screen "
        f"that is still travelling (BE-0326's non-inertial contract): frames at "
        f"{[f'+{dt * 1000:.0f}ms {m:.1%} moved' for dt, m in still_moving]} differ from the settled "
        "screen outside the scroll-indicator strip"
    )
