"""Tests for the RocketSim backend: parsing and semantic/coordinate tap.

The assumed schema is exercised here; on a real machine it may need adjustment.
"""

from __future__ import annotations

from simpilot.drivers import base
from simpilot.drivers.rocketsim import RocketSimDriver, parse_elements, tap_id_cmd, tap_xy_cmd

WRAPPED = """
{"rs":"1","elements":[
  {"identifier":"settings.open","label":"設定","traits":["button"],
   "frame":{"x":0,"y":0,"width":100,"height":40}},
  {"label":"no id","traits":["button"],"frame":[10,20,30,40]}
]}
"""

ARRAY = '[{"identifier":"a","label":"A","traits":["button"],"frame":{"x":0,"y":0,"width":2,"height":2}}]'


def test_parse_wrapped_and_array() -> None:
    els = parse_elements(WRAPPED)
    assert len(els) == 2
    assert els[0]["identifier"] == "settings.open"
    assert els[0]["frame"] == (0.0, 0.0, 100.0, 40.0)
    assert els[1]["identifier"] is None
    assert els[1]["frame"] == (10.0, 20.0, 30.0, 40.0)  # list-form frame

    assert parse_elements(ARRAY)[0]["identifier"] == "a"


def test_semantic_tap_by_id() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "elements" in args:
            return WRAPPED
        calls.append(args)
        return ""

    RocketSimDriver("U", run=run).tap({"id": "settings.open"})
    assert calls == [tap_id_cmd("U", "settings.open")]


def test_coordinate_tap_when_no_id() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "elements" in args:
            return WRAPPED
        calls.append(args)
        return ""

    RocketSimDriver("U", run=run).tap({"label": "no id"})
    # frame [10,20,30,40] -> center (25, 40)
    assert calls == [tap_xy_cmd("U", 25, 40)]


def test_capabilities_has_semantic_tap() -> None:
    caps = RocketSimDriver("U", run=lambda a: "[]").capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.NETWORK in caps
