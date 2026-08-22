"""Run the driver conformance contract (BE-0114) against the backends the fast gate can drive.

`FakeDriver` needs no Simulator or browser, so its conformance runs here on every PR on Linux.
The Playwright (web CI) and XCUITest (on-device E2E) backends reuse the same contract from
`driver_conformance` under their heavier paths.
"""

from __future__ import annotations

import pytest
from driver_conformance import (
    FIELD_ID,
    OBSTRUCTION_CLEAR_ID,
    OBSTRUCTION_COVER_ID,
    OBSTRUCTION_TARGET_ID,
    SCROLL_ROW_COUNT,
    SCROLL_ROW_PREFIX,
    SCROLL_TALL_ID,
    SECURE_FIELD_ID,
    TAP_MIRROR_A_ID,
    TAP_MIRROR_B_ID,
    ConformanceHarness,
    DriverConformanceContract,
    element,
)

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver, React

# The scroll conformance screen's geometry on the fake (BE-0326): a 300x800 viewport over content
# taller than it. Rows of `_ROW_H` stack from the top, so the lower rows start below the fold; the
# tall row exceeds the viewport, so only its center — not its whole frame — can land on-screen.
_SCROLL_VIEWPORT: base.Point = (300.0, 800.0)
_ROW_W, _ROW_H = 280.0, 90.0
_TALL_H = 1400.0

# The conformance field's frame on the fake screen: a known, off-origin box so a coordinate tap at
# its center is unambiguous and never coincides with the default (0,0)-origin seeded elements.
_FIELD_FRAME: base.Frame = (0.0, 200.0, 100.0, 40.0)

# The masked field's frame (BE-0331): clear of both the seeded elements and the plain field above,
# so neither the coordinate tap nor the obstruction case ever resolves to it by accident.
_SECURE_FIELD_FRAME: base.Frame = (0.0, 300.0, 100.0, 40.0)


def _secure_field() -> base.Element:
    return element(
        identifier=SECURE_FIELD_ID,
        value="",
        traits=[base.Trait.SECURE_TEXT_FIELD],
        frame=_SECURE_FIELD_FRAME,
    )


def _mirror(identifier: str, frame: base.Frame) -> base.Element:
    return element(identifier=identifier, value="0", frame=frame)


def _conformance_react(
    field: base.Element, mirrors: dict[str, base.Element] | None = None
) -> React:
    """Model the conformance field and tap mirrors, so the gate observes real state, not just a log.

    The on-device and web backends surface a live editable field; `FakeDriver` records actions but
    holds no field state, so the round-trip / focus invariants (BE-0280) would be unobservable on
    the fast gate. This `react` gives the fake just enough field behavior — focus follows the last
    tap, typing appends to the focused field, deleting trims its end — to exercise them for real.

    `mirrors` (BE-0339 Unit 6) does the same for the two tap-mirror targets: each carries its own
    tap count in `value`, incremented only when *that* target is the one tapped — the fake's
    twin of `LogScreen.kt`'s `log.longpress.value` mirroring, so "the element that reacted is the
    element named" is observable on the fast gate too.
    """
    focused = {"on": False}
    mirrors = mirrors or {}

    def react(_driver: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            focused["on"] = isinstance(arg, dict) and arg.get("id") == FIELD_ID
            if isinstance(arg, dict) and (mirror := mirrors.get(arg.get("id", ""))) is not None:
                mirror["value"] = str(int(mirror["value"] or "0") + 1)
        elif kind == "tap_point" and isinstance(arg, tuple):
            x, y, w, h = field["frame"]
            px, py = arg
            focused["on"] = x <= px <= x + w and y <= py <= y + h
        elif kind == "type" and focused["on"] and isinstance(arg, str):
            field["value"] = (field["value"] or "") + arg
        elif kind == "delete_text" and focused["on"] and isinstance(arg, int):
            field["value"] = (field["value"] or "")[: -arg or None]

    return react


class FakeConformanceHarness:
    """Realizes a conformance screen as a `FakeDriver` seeded with those elements.

    Every screen also carries the always-present conformance field (BE-0280) and the two tap-mirror
    targets (BE-0339 Unit 6), wired to a `react` that models their state, so the text-editing,
    `tap_point`, and tap-identity invariants are all observable on the fast gate exactly as they are
    against a real field or a real device on-device.
    """

    backend = "fake"

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        field = element(identifier=FIELD_ID, value="", frame=_FIELD_FRAME)
        mirror_a = _mirror(TAP_MIRROR_A_ID, (0.0, 400.0, 100.0, 40.0))
        mirror_b = _mirror(TAP_MIRROR_B_ID, (0.0, 460.0, 100.0, 40.0))
        return FakeDriver(
            screen=[*elements, field, _secure_field(), mirror_a, mirror_b],
            react=_conformance_react(field, {TAP_MIRROR_A_ID: mirror_a, TAP_MIRROR_B_ID: mirror_b}),
        )

    def scrollable_screen(self) -> base.Driver:
        # A FakeDriver in scrollable mode (BE-0326): rows and a taller-than-viewport row in content
        # coordinates, over a fixed viewport. `scroll` pans a clamped offset and `query()` reports
        # frames translated by it, so the lower rows and the tall row start with off-screen centers.
        rows = [
            element(identifier=f"{SCROLL_ROW_PREFIX}{i}", frame=(0.0, i * _ROW_H, _ROW_W, _ROW_H))
            for i in range(SCROLL_ROW_COUNT)
        ]
        tall = element(
            identifier=SCROLL_TALL_ID, frame=(0.0, SCROLL_ROW_COUNT * _ROW_H, _ROW_W, _TALL_H)
        )
        return FakeDriver(screen=[*rows, tall], viewport=_SCROLL_VIEWPORT)

    def obstruction_screen(self) -> base.Driver:
        # `cover` is listed after `target` (document order) and is wider than it (300 vs. 100), so it
        # is neither a subset nor a superset of `target`'s frame — read as a genuine covering
        # element, not a descendant or an ancestor (`topmost_at_point`'s own contract).
        field = element(identifier=FIELD_ID, value="", frame=_FIELD_FRAME)
        target = element(identifier=OBSTRUCTION_TARGET_ID, frame=(0.0, 0.0, 100.0, 20.0))
        cover = element(identifier=OBSTRUCTION_COVER_ID, frame=(0.0, 0.0, 300.0, 15.0))
        clear = element(identifier=OBSTRUCTION_CLEAR_ID, frame=(0.0, 500.0, 100.0, 20.0))
        return FakeDriver(
            screen=[target, cover, clear, field, _secure_field()],
            react=_conformance_react(field),
        )


class TestFakeDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self) -> ConformanceHarness:
        return FakeConformanceHarness()
