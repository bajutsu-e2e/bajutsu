"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import make_driver, select_actuator
from bajutsu.drivers import base


def test_select_first_available() -> None:
    assert select_actuator(["rocketsim", "idb"], available=lambda b: True) == "rocketsim"


def test_select_falls_through_to_available() -> None:
    # RocketSim unavailable (e.g. headless CI) -> idb.
    assert select_actuator(["rocketsim", "idb"], available=lambda b: b == "idb") == "idb"


def test_select_none_available_raises() -> None:
    with pytest.raises(RuntimeError):
        select_actuator(["rocketsim", "idb"], available=lambda b: False)


def test_make_driver() -> None:
    # Both backends actuate by coordinates (rocketsim's protocol has no usable
    # identifier), so neither advertises a semantic tap.
    idb = make_driver("idb", "U")
    assert idb.name == "idb"
    assert base.Capability.SEMANTIC_TAP not in idb.capabilities()
    rs = make_driver("rocketsim", "U")
    assert rs.name == "rocketsim"
    assert base.Capability.SEMANTIC_TAP not in rs.capabilities()
    assert base.Capability.QUERY in rs.capabilities()


def test_make_driver_unknown() -> None:
    with pytest.raises(ValueError):
        make_driver("bogus", "U")
