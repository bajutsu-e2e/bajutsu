"""Tests for the idb backend: describe-all parsing, commands, coordinate tap."""

from __future__ import annotations

from simyoke.drivers import base
from simyoke.drivers.idb import (
    IdbDriver,
    parse_describe_all,
    tap_cmd,
)

FIXTURE = """
[
  {"AXUniqueId":"settings.open","AXLabel":"設定","type":"Button","enabled":true,
   "frame":{"x":0,"y":0,"width":100,"height":40}},
  {"AXUniqueId":"submit","AXLabel":"送信","type":"Button","enabled":false,
   "frame":{"x":0,"y":50,"width":100,"height":40}},
  {"AXLabel":"static","type":"StaticText","frame":{"x":0,"y":100,"width":100,"height":20}}
]
"""

NDJSON = (
    '{"AXUniqueId":"a","AXLabel":"A","type":"Button","frame":{"x":1,"y":2,"width":3,"height":4}}\n'
    '{"AXUniqueId":"b","AXLabel":"B","type":"Cell","frame":{"x":0,"y":0,"width":1,"height":1}}\n'
)


def test_parse_describe_all() -> None:
    els = parse_describe_all(FIXTURE)
    assert len(els) == 3
    assert els[0]["identifier"] == "settings.open"
    assert els[0]["label"] == "設定"
    assert els[0]["traits"] == ["button"]
    assert els[0]["frame"] == (0.0, 0.0, 100.0, 40.0)
    assert base.Trait.NOT_ENABLED in els[1]["traits"]  # enabled: false
    assert els[2]["identifier"] is None  # static text, no AXUniqueId


def test_parse_ndjson_fallback() -> None:
    els = parse_describe_all(NDJSON)
    assert [e["identifier"] for e in els] == ["a", "b"]


def test_tap_resolves_frame_center() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            return FIXTURE
        calls.append(args)
        return ""

    driver = IdbDriver("U", run=run)
    driver.tap({"id": "settings.open"})
    # center of (0,0,100,40) -> (50, 20)
    assert calls == [tap_cmd("U", 50, 20)]
    assert calls[0] == ["idb", "ui", "tap", "--udid", "U", "50", "20"]


def test_capabilities_has_no_semantic_tap() -> None:
    assert base.Capability.SEMANTIC_TAP not in IdbDriver("U", run=lambda a: "[]").capabilities()
