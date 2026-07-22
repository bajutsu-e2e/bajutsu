"""Tests for the vision tab locator (bajutsu/crawl/tabs.py, BE-0038).

No LLM: the Claude call is behind the vendor-neutral backend (BE-0104), exercised here with a fake
backend — the same way the alert locator is tested. We check the tree-exposes-tabs gate and that
pixel coordinates from the tool call are normalized to [0,1] against the screenshot size.
"""

from __future__ import annotations

import struct

from conftest import FakeBackend, FakeBlock, el

from bajutsu.agents.ai_config import AiConfig
from bajutsu.ai.base import AnyTool, ImagePart, TextPart
from bajutsu.crawl import tabs as crawl_tabs


def _png(width: int, height: int) -> bytes:
    """A minimal PNG whose IHDR advertises the given pixel size (enough for png_size)."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr


def test_needs_vision_tabs_only_for_an_unaddressable_tab_bar() -> None:
    # SwiftUI TabView as the accessibility tree surfaces it: a lone group labelled "Tab Bar", no id, no per-tab child.
    bar = [el(label="Tab Bar", traits=["group"]), el(identifier="home.start", traits=["button"])]
    assert crawl_tabs.tab_bar_present(bar) is True
    assert crawl_tabs.addressable_tabs(bar) is False
    assert crawl_tabs.needs_vision_tabs(bar) is True
    # Tabs already addressable (a `tab` element with an id) -> no vision needed.
    addr = [el(identifier="tab.home", traits=["tab"]), el(identifier="tab.me", traits=["tab"])]
    assert crawl_tabs.addressable_tabs(addr) is True
    assert crawl_tabs.needs_vision_tabs(addr) is False
    # No tab bar at all -> vision never fires on an ordinary screen.
    plain = [el(identifier="b", traits=["button"])]
    assert crawl_tabs.tab_bar_present(plain) is False
    assert crawl_tabs.needs_vision_tabs(plain) is False
    assert crawl_tabs.needs_vision_tabs([]) is False


def test_uikit_tab_bar_is_provisional_and_routes_to_vision_for_now() -> None:
    """UIKit support is a stub: `_uikit_addressable_tabs` recognizes nothing yet, so a UIKit tab bar
    (its tabs exposed as their own button elements) currently falls back to vision. When the stub is
    completed to recognize those tabs, `addressable_tabs` flips True and vision is skipped — this
    test then documents the change."""
    assert crawl_tabs._uikit_addressable_tabs([el(label="Home", traits=["button"])]) is False
    uikit = [
        el(traits=["tabBar"], frame=(0, 800, 400, 80)),  # the bar container
        el(label="Home", traits=["button"], frame=(0, 800, 200, 80)),
        el(label="Me", traits=["button"], frame=(200, 800, 200, 80)),
    ]
    assert crawl_tabs.tab_bar_present(uikit) is True
    assert crawl_tabs.addressable_tabs(uikit) is False  # provisional: not yet recognized
    assert crawl_tabs.needs_vision_tabs(uikit) is True


def test_locator_normalizes_pixel_coordinates() -> None:
    backend = FakeBackend(
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
    tabs = crawl_tabs.ClaudeTabLocator(backend=backend).locate(_png(600, 2000))
    assert [t.label for t in tabs] == ["Home", "Me"]
    assert abs(tabs[0].x - 100 / 600) < 1e-6 and abs(tabs[0].y - 1900 / 2000) < 1e-6
    request = backend.requests[0]
    assert isinstance(request.tool_choice, AnyTool)
    content = request.messages[0].content
    assert any(isinstance(c, ImagePart) for c in content)
    text = next(c.text for c in content if isinstance(c, TextPart))
    assert "600x2000" in text


def test_locator_returns_empty_when_no_tab_bar() -> None:
    tabs = crawl_tabs.ClaudeTabLocator(backend=FakeBackend(FakeBlock("find_tabs", {"tabs": []})))
    assert tabs.locate(_png(10, 10)) == []
    # No tool call at all -> also empty (best-effort, never crashes the crawl).
    assert crawl_tabs.ClaudeTabLocator(backend=FakeBackend()).locate(_png(10, 10)) == []


# --- BE-0097: the tab locator honours the user's AI provider config ---


def test_locator_threads_ai_config_to_model() -> None:
    """BE-0097: a non-default AiConfig is threaded to resolve_model, so the tab locator talks to the
    user's configured provider."""
    ai = AiConfig(model="us.anthropic.claude-opus-4-8-v1")
    backend = FakeBackend(FakeBlock("find_tabs", {"tabs": []}))
    crawl_tabs.ClaudeTabLocator(backend=backend, ai=ai).locate(_png(10, 10))
    assert backend.requests[0].model == "us.anthropic.claude-opus-4-8-v1"


def test_output_language_is_folded_into_the_tab_locator_prompt() -> None:
    # BE-0188: the tab locator's system prompt carries the language instruction; `auto` (default)
    # leaves it unchanged.
    default = FakeBackend(FakeBlock("find_tabs", {"tabs": []}))
    crawl_tabs.ClaudeTabLocator(backend=default).locate(_png(10, 10))
    assert "日本語" not in default.requests[0].system

    ja = FakeBackend(FakeBlock("find_tabs", {"tabs": []}))
    crawl_tabs.ClaudeTabLocator(backend=ja, ai=AiConfig(language="ja")).locate(_png(10, 10))
    assert "日本語" in ja.requests[0].system
