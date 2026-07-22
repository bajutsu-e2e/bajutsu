"""Run the driver conformance contract (BE-0114) against the backends the fast gate can drive.

`FakeDriver` needs no Simulator or browser, so its conformance runs here on every PR on Linux.
The Playwright (web CI) and XCUITest (on-device E2E) backends reuse the same contract from
`driver_conformance` under their heavier paths.
"""

from __future__ import annotations

import pytest
from driver_conformance import FIELD_ID, ConformanceHarness, DriverConformanceContract, element

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver, React

# The conformance field's frame on the fake screen: a known, off-origin box so a coordinate tap at
# its center is unambiguous and never coincides with the default (0,0)-origin seeded elements.
_FIELD_FRAME: base.Frame = (0.0, 200.0, 100.0, 40.0)


def _text_field_react(field: base.Element) -> React:
    """Model the conformance field so the gate observes the real text round-trip, not just a log.

    The on-device and web backends surface a live editable field; `FakeDriver` records actions but
    holds no field state, so the round-trip / focus invariants (BE-0280) would be unobservable on
    the fast gate. This `react` gives the fake just enough field behavior — focus follows the last
    tap, typing appends to the focused field, deleting trims its end — to exercise them for real.
    """
    focused = {"on": False}

    def react(_driver: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            focused["on"] = isinstance(arg, dict) and arg.get("id") == FIELD_ID
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

    Every screen also carries the always-present conformance field (BE-0280), wired to a `react`
    that models its text state, so the text-editing and `tap_point` invariants are observable on the
    fast gate exactly as they are against a real field on-device.
    """

    backend = "fake"

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        field = element(identifier=FIELD_ID, value="", frame=_FIELD_FRAME)
        return FakeDriver(screen=[*elements, field], react=_text_field_react(field))


class TestFakeDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self) -> ConformanceHarness:
        return FakeConformanceHarness()
