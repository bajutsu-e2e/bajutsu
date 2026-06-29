"""Tests for WebContextDriver — the driver wrapper that resolves against DOM elements."""

from __future__ import annotations

import pytest

from bajutsu.drivers import base
from bajutsu.webview import WebContextDriver


class FakeBridge:
    """Fake bridge returning canned elements and recording taps/types."""

    def __init__(self, elements: list[base.Element]) -> None:
        self._elements = elements
        self.tapped: list[tuple[str, base.Point]] = []
        self.typed: list[tuple[str, str]] = []
        self.scrolled: list[tuple[str, str]] = []

    def query_dom(self, webview_id: str) -> list[base.Element]:
        return self._elements

    def tap_element(self, webview_id: str, point: base.Point) -> None:
        self.tapped.append((webview_id, point))

    def type_text(self, webview_id: str, text: str) -> None:
        self.typed.append((webview_id, text))

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        self.scrolled.append((webview_id, element_id))


def _el(identifier: str, frame: base.Frame = (10.0, 20.0, 100.0, 40.0)) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "value": None,
        "traits": [],
        "frame": frame,
    }


def test_query_returns_dom_elements() -> None:
    elements = [_el("btn-ok"), _el("btn-cancel")]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="checkout.wv")
    assert driver.query() == elements


def test_tap_resolves_and_dispatches() -> None:
    elements = [_el("place-order", frame=(10.0, 20.0, 100.0, 40.0))]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="checkout.wv")
    driver.tap({"id": "place-order"})
    assert len(bridge.tapped) == 1
    wv_id, point = bridge.tapped[0]
    assert wv_id == "checkout.wv"
    assert point == (60.0, 40.0)  # center of (10, 20, 100, 40)


def test_tap_ambiguous_fails() -> None:
    elements = [_el("dup"), _el("dup")]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    with pytest.raises(base.AmbiguousSelector):
        driver.tap({"id": "dup"})


def test_tap_not_found_fails() -> None:
    bridge = FakeBridge([])
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    with pytest.raises(base.ElementNotFound):
        driver.tap({"id": "missing"})


def test_unsupported_actions_raise() -> None:
    bridge = FakeBridge([])
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    with pytest.raises(base.UnsupportedAction):
        driver.swipe((0, 0), (100, 100))
    with pytest.raises(base.UnsupportedAction):
        driver.pinch({"id": "x"}, 2.0)
    with pytest.raises(base.UnsupportedAction):
        driver.rotate({"id": "x"}, 1.0)


def test_capabilities_include_webview() -> None:
    bridge = FakeBridge([])
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    caps = driver.capabilities()
    assert base.Capability.WEBVIEW in caps
    assert base.Capability.QUERY in caps


def test_double_tap_dispatches() -> None:
    elements = [_el("btn", frame=(0.0, 0.0, 80.0, 40.0))]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    driver.double_tap({"id": "btn"})
    assert len(bridge.tapped) == 1


def test_wait_for_polls_bridge() -> None:
    elements = [_el("loaded")]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    assert driver.wait_for({"id": "loaded"}, timeout=1.0)


def test_wait_for_not_found() -> None:
    bridge = FakeBridge([])
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    assert not driver.wait_for({"id": "missing"}, timeout=0.1)


def test_type_text_dispatches() -> None:
    bridge = FakeBridge([])
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    driver.type_text("hello@example.com")
    assert bridge.typed == [("wv", "hello@example.com")]


def test_scroll_to_before_tap() -> None:
    elements = [_el("below-fold", frame=(10.0, 2000.0, 100.0, 40.0))]
    bridge = FakeBridge(elements)
    driver = WebContextDriver(bridge=bridge, webview_id="wv")
    driver.tap({"id": "below-fold"})
    assert len(bridge.scrolled) == 1
    assert bridge.scrolled[0] == ("wv", "below-fold")
    assert len(bridge.tapped) == 1
