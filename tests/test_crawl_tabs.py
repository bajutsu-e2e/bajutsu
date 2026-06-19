"""Tests for the vision tab locator (bajutsu/crawl_tabs.py, BE-0038).

No LLM: the Claude call is behind the Anthropic SDK shape, exercised here with a fake client — the
same way the alert locator is tested. We check the tree-exposes-tabs gate and that pixel
coordinates from the tool call are normalized to [0,1] against the screenshot size.
"""

from __future__ import annotations

import struct

from conftest import FakeAnthropic, FakeBlock, el

from bajutsu import crawl_tabs


def _png(width: int, height: int) -> bytes:
    """A minimal PNG whose IHDR advertises the given pixel size (enough for _png_size)."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr


def test_tree_exposes_tabs_detects_a_tab_trait() -> None:
    assert crawl_tabs.tree_exposes_tabs([el(identifier="t", traits=["tab"])]) is True
    # A plain button bar (no `tab` trait) is not addressable as tabs -> needs the vision fallback.
    assert crawl_tabs.tree_exposes_tabs([el(identifier="b", traits=["button"])]) is False
    assert crawl_tabs.tree_exposes_tabs([]) is False


def test_locator_normalizes_pixel_coordinates() -> None:
    client = FakeAnthropic(
        FakeBlock(
            "find_tabs",
            {
                "tabs": [
                    {"x": 100, "y": 1900, "label": "Home"},
                    {"x": 500, "y": 1900, "label": "Me"},
                    {"y": 1900},  # missing x -> skipped (can't tap)
                ]
            },
        )
    )
    tabs = crawl_tabs.ClaudeTabLocator(client=client).locate(_png(600, 2000))
    assert [t.label for t in tabs] == ["Home", "Me"]
    assert abs(tabs[0].x - 100 / 600) < 1e-6 and abs(tabs[0].y - 1900 / 2000) < 1e-6
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "any"}
    content = call["messages"][0]["content"]
    assert any(c["type"] == "image" for c in content)
    text = next(c["text"] for c in content if c["type"] == "text")
    assert "600x2000" in text


def test_locator_returns_empty_when_no_tab_bar() -> None:
    tabs = crawl_tabs.ClaudeTabLocator(client=FakeAnthropic(FakeBlock("find_tabs", {"tabs": []})))
    assert tabs.locate(_png(10, 10)) == []
    # No tool call at all -> also empty (best-effort, never crashes the crawl).
    assert crawl_tabs.ClaudeTabLocator(client=FakeAnthropic()).locate(_png(10, 10)) == []
