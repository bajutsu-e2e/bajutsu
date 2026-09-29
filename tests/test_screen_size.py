"""`screen_size`: the one screen-size definition every coordinate path scales by."""

from __future__ import annotations

from typing import Any, cast

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import screen_size
from bajutsu.common.drivers.fake import FakeDriver


def _el(w: float, h: float) -> base.Element:
    return {"identifier": None, "label": None, "traits": [], "value": None,
            "frame": (0.0, 0.0, w, h), "nativeZ": None}  # fmt: skip


class _TreeOnly:
    """A driver with no `viewport()`: the tree's extent is all it can offer."""

    def __init__(self, tree: list[base.Element]) -> None:
        self.tree = tree
        self.queries = 0

    def query(self) -> list[base.Element]:
        self.queries += 1
        return self.tree


def test_a_reported_viewport_wins_over_an_oversized_tree() -> None:
    class _PointViewport(FakeDriver):
        def viewport(self) -> base.Point:
            return (402.0, 874.0)

    driver = _PointViewport(screen=[_el(1206.0, 2622.0)])
    assert screen_size(driver) == (402.0, 874.0)


def test_without_a_viewport_it_falls_back_to_the_tree_extent() -> None:
    driver = _TreeOnly([_el(390.0, 844.0)])
    assert screen_size(cast(Any, driver)) == (390.0, 844.0)
    assert driver.queries == 1


def test_a_tree_the_caller_already_holds_is_not_read_again() -> None:
    driver = _TreeOnly([_el(1.0, 1.0)])
    assert screen_size(cast(Any, driver), [_el(320.0, 568.0)]) == (320.0, 568.0)
    assert driver.queries == 0
