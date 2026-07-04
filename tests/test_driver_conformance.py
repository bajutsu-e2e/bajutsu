"""Run the driver conformance contract (BE-0114) against the backends the fast gate can drive.

`FakeDriver` needs no Simulator or browser, so its conformance runs here on every PR on Linux.
The Playwright (web CI) and idb / XCUITest (on-device E2E) backends reuse the same contract from
`driver_conformance` under their heavier paths.
"""

from __future__ import annotations

import pytest
from driver_conformance import ConformanceHarness, DriverConformanceContract

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver


class FakeConformanceHarness:
    """Realizes a conformance screen as a `FakeDriver` seeded with those elements."""

    backend = "fake"

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        return FakeDriver(screen=elements)


class TestFakeDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self) -> ConformanceHarness:
        return FakeConformanceHarness()
