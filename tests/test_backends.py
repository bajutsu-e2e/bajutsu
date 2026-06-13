"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import make_driver, select_actuator
from bajutsu.drivers import base


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (["idb"], "idb"),
        # unknown backends are never selected, even when reported "available"
        (["bogus", "idb"], "idb"),
    ],
)
def test_select_actuator_picks_first_known_available(order: list[str], expected: str) -> None:
    assert select_actuator(order, available=lambda b: True) == expected


def test_select_none_available_raises() -> None:
    with pytest.raises(RuntimeError):
        select_actuator(["idb"], available=lambda b: False)


def test_make_driver() -> None:
    # idb actuates by coordinates (resolving each element's frame center), so it
    # does not advertise a semantic tap.
    idb = make_driver("idb", "U")
    assert idb.name == "idb"
    assert base.Capability.QUERY in idb.capabilities()
    assert base.Capability.SEMANTIC_TAP not in idb.capabilities()


def test_make_driver_unknown() -> None:
    with pytest.raises(ValueError):
        make_driver("bogus", "U")
