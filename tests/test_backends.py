"""Tests for backend selection and driver construction."""

from __future__ import annotations

import pytest

from bajutsu.backends import make_driver, select_actuator
from bajutsu.drivers import base


def test_select_first_available() -> None:
    assert select_actuator(["idb"], available=lambda b: True) == "idb"


def test_select_skips_unknown_backends() -> None:
    # Unknown backends are never selected, even when reported "available".
    assert select_actuator(["bogus", "idb"], available=lambda b: True) == "idb"


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
