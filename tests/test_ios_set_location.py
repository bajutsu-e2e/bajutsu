"""`setLocation` through the XCUITest runner's `/location` route (BE-0475).

Covers the scenario model's coordinate bounds and the driver's mapping of the runner's replies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import ValidationError

from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import XcuitestChannelError, XcuitestDriver, _Reply
from bajutsu.common.scenario import SetLocation


class _Runner:
    """A fake transport that records every call and answers each with one fixed reply."""

    def __init__(self, status: str = "ok", raw: bytes = b"") -> None:
        self.status = status
        self.raw = raw
        self.calls: list[tuple[str, str, Mapping[str, Any] | None]] = []

    def __call__(self, method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        self.calls.append((method, path, body))
        return _Reply(status=self.status, raw=self.raw)


# --- scenario model ---


@pytest.mark.parametrize(
    ("lat", "lon"), [(90.5, 0.0), (-91.0, 0.0), (0.0, 180.5), (0.0, -181.0), (float("nan"), 0.0)]
)
def test_an_impossible_coordinate_fails_when_the_scenario_loads(lat: float, lon: float) -> None:
    # Rejected up front on every backend, rather than as the runner's 400 mid-run.
    with pytest.raises(ValidationError):
        SetLocation(lat=lat, lon=lon)


def test_the_boundary_coordinates_load() -> None:
    assert SetLocation(lat=-90, lon=180).lat == -90


# --- driver ---


def test_set_location_posts_the_coordinate() -> None:
    runner = _Runner()
    XcuitestDriver(transport=runner).set_location(35.6812, 139.7671)
    assert runner.calls == [("POST", "/location", {"lat": 35.6812, "lon": 139.7671})]


def test_clear_location_deletes_without_a_body() -> None:
    runner = _Runner()
    XcuitestDriver(transport=runner).clear_location()
    assert runner.calls == [("DELETE", "/location", None)]


@pytest.mark.parametrize("call", ["set", "clear"])
def test_unsupported_names_the_ios_and_xcode_floor(call: str) -> None:
    driver = XcuitestDriver(transport=_Runner("unsupported"))
    with pytest.raises(base.UnsupportedAction, match=r"iOS 16\.4.*Xcode 16\.3"):
        driver.set_location(1.0, 2.0) if call == "set" else driver.clear_location()


@pytest.mark.parametrize(("call", "verb"), [("set", "setting"), ("clear", "clearing")])
def test_a_runner_predating_the_route_is_a_channel_error_naming_the_rebuild(
    call: str, verb: str
) -> None:
    # A runner build without `/location` answers 404 with the router's own `unknown endpoint`.
    raw = b'{"status":"error","message":"unknown endpoint"}'
    driver = XcuitestDriver(transport=_Runner("error", raw))
    with pytest.raises(XcuitestChannelError, match=rf"{verb} the location.*make runner-build"):
        driver.set_location(1.0, 2.0) if call == "set" else driver.clear_location()


def test_a_raised_assignment_is_a_channel_error_not_the_floor() -> None:
    # The runner reports an assignment that raised as `error`: a runner fault, which must neither
    # read as the iOS / Xcode floor nor send the author to rebuild a current runner.
    driver = XcuitestDriver(transport=_Runner("error", b'{"status":"error"}'))
    with pytest.raises(XcuitestChannelError, match=r"setting the location \(status=error\)$"):
        driver.set_location(1.0, 2.0)
