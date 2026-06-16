"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import make_driver, resolve_actuators, select_actuator
from bajutsu.drivers import base


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["idb"], "idb"),
        # unknown backends are never selected, even when reported "available"
        (["bogus", "idb"], "idb"),
        # a platform token expands to its actuators (ios -> idb)
        (["ios"], "idb"),
        (["fake"], "fake"),
    ],
)
def test_select_actuator_picks_first_known_available(order: list[str], expected: str) -> None:
    assert select_actuator(order, available=lambda b: True) == expected


def test_select_actuator_falls_through_unavailable_platform() -> None:
    # android resolves to adb (unavailable by default), so a request that lists fake after it
    # falls through to fake — no forced availability needed.
    assert select_actuator(["android", "fake"]) == "fake"


def test_resolve_actuators_expands_platforms() -> None:
    # Platform tokens expand to their actuators; bare actuators and unknowns pass through.
    assert resolve_actuators(["ios", "android", "web", "fake"]) == [
        "idb",
        "adb",
        "playwright",
        "fake",
    ]
    assert resolve_actuators(["idb", "bogus"]) == ["idb", "bogus"]


def test_select_none_available_raises() -> None:
    with pytest.raises(RuntimeError):
        select_actuator(["idb"], available=lambda b: False)


def test_select_planned_backend_reports_not_implemented() -> None:
    # android resolves to adb, which is recognized but has no driver yet — a clear error,
    # not a generic "no available actuator".
    with pytest.raises(RuntimeError, match="not implemented yet"):
        select_actuator(["android"])
    with pytest.raises(RuntimeError, match="not implemented yet"):
        select_actuator(["web"])


def test_fake_is_always_available() -> None:
    # The fake backend needs no executable, so it selects without any device tooling.
    assert select_actuator(["fake"]) == "fake"


def test_make_driver() -> None:
    # idb actuates by coordinates (resolving each element's frame center), so it
    # does not advertise a semantic tap.
    idb = make_driver("idb", "U")
    assert idb.name == "idb"
    assert base.Capability.QUERY in idb.capabilities()
    assert base.Capability.SEMANTIC_TAP not in idb.capabilities()


def test_make_driver_fake() -> None:
    # The fake driver is constructible without a device — used by the in-process demos/tests.
    fake = make_driver("fake", "U")
    assert fake.name == "fake"
    assert base.Capability.QUERY in fake.capabilities()


def test_make_driver_planned_backend() -> None:
    # A recognized-but-unimplemented actuator raises NotImplementedError (distinct from an
    # outright-unknown token), so the message can point at the multi-platform design.
    with pytest.raises(NotImplementedError, match="not implemented yet"):
        make_driver("adb", "U")


def test_make_driver_unknown() -> None:
    with pytest.raises(ValueError, match="bogus"):
        make_driver("bogus", "U")
