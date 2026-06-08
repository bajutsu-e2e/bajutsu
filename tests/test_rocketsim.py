"""Tests for the RocketSim backend: rs/1 debug parsing, coordinate actuation, and
identifier recovery via an idmap (rocketsim exposes no accessibilityIdentifier)."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.rocketsim import RocketSimDriver, parse_elements, tap_cmd
from bajutsu.idmap import Matcher

# `elements --agent-mode debug`: frame is [[x, y], [w, h]]; no identifier field.
DEBUG = """
{"data":{"elements":[
  {"frame":[[0,0],[100,40]],"id":1,"label":"Settings","role":"button"},
  {"frame":[[32,427],[338,52]],"id":9,"label":"Item 3","role":"staticText"},
  {"frame":[[159,177],[66,20]],"id":3,"label":"Count: 2","role":"staticText","value":"2"}
]},"ok":true,"rs":"1"}
"""


def test_parse_debug_format() -> None:
    els = parse_elements(DEBUG)
    assert len(els) == 3
    # No accessibilityIdentifier exists in the protocol -> always None until idmap.
    assert all(e["identifier"] is None for e in els)
    assert els[0]["frame"] == (0.0, 0.0, 100.0, 40.0)  # nested [[x,y],[w,h]]
    assert els[0]["traits"] == ["button"]
    assert els[2]["value"] == "2"


def test_coordinate_tap_by_label() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "elements" in args:
            return DEBUG
        calls.append(args)
        return ""

    RocketSimDriver("U", run=run).tap({"label": "Settings"})
    # frame (0,0,100,40) -> center (50, 20)
    assert calls == [tap_cmd("U", 50, 20)]


def test_idmap_recovers_identifier_then_taps() -> None:
    """An id-first selector resolves once the idmap fills the identifier in query()."""
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "elements" in args:
            return DEBUG
        calls.append(args)
        return ""

    idmap = {
        "nav.settings": Matcher(role="button", label="Settings"),
        "counter.value": Matcher(role="staticText", label_matches="^Count:"),
    }
    driver = RocketSimDriver("U", run=run, idmap=idmap)

    els = {e["identifier"]: e for e in driver.query()}
    assert "nav.settings" in els and "counter.value" in els

    driver.tap({"id": "nav.settings"})
    assert calls == [tap_cmd("U", 50, 20)]


def test_capabilities_are_coordinate_only() -> None:
    caps = RocketSimDriver("U", run=lambda a: '{"data":{"elements":[]}}').capabilities()
    assert base.Capability.QUERY in caps
    assert base.Capability.SEMANTIC_TAP not in caps  # no usable identifier -> coords
    assert base.Capability.NETWORK not in caps
    assert base.Capability.MULTI_TOUCH not in caps
