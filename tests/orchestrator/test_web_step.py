"""Tests for the web (WebView context) step in the run loop."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


class FakeBridge:
    """A fake bridge for run-loop tests: returns canned DOM elements and records taps."""

    def __init__(self, dom_elements: list[base.Element]) -> None:
        self._elements = dom_elements
        self.tapped: list[tuple[str, base.Point]] = []

    def query_dom(self, webview_id: str) -> list[base.Element]:
        return self._elements

    def tap_element(self, webview_id: str, point: base.Point) -> None:
        self.tapped.append((webview_id, point))


def test_web_step_taps_dom_element() -> None:
    native_screen = [el("checkout.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        {
            "identifier": "place-order",
            "label": "Place Order",
            "traits": ["button"],
            "value": None,
            "frame": (50.0, 100.0, 200.0, 40.0),
        },
    ]
    bridge = FakeBridge(dom_elements)
    driver = FakeDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "web tap",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "checkout.webview"},
                            "steps": [{"tap": {"id": "place-order"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert len(bridge.tapped) == 1
    assert bridge.tapped[0][0] == "checkout.webview"
    assert bridge.tapped[0][1] == (150.0, 120.0)


def test_web_step_assert_inside_web_context() -> None:
    native_screen = [el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        {
            "identifier": "confirmation",
            "label": "Order placed",
            "traits": [],
            "value": None,
            "frame": (10.0, 10.0, 100.0, 20.0),
        },
    ]
    bridge = FakeBridge(dom_elements)
    driver = FakeDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "web assert",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [
                                {"assert": [{"exists": {"id": "confirmation"}}]},
                            ],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure


def test_web_step_host_not_found_fails() -> None:
    driver = FakeDriver([el("other.element")])
    bridge = FakeBridge([])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "missing host",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "nonexistent.webview"},
                            "steps": [{"tap": {"id": "ok"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert not result.ok
    assert "一致なし" in (result.failure or "")


def test_web_step_no_bridge_fails() -> None:
    driver = FakeDriver([el("wv")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "no bridge",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "wv"},
                            "steps": [{"tap": {"id": "ok"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "bridge" in (result.failure or "").lower()


def test_web_step_dom_element_not_found() -> None:
    native_screen = [el("wv", frame=(0.0, 0.0, 400.0, 800.0))]
    bridge = FakeBridge([])
    driver = FakeDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "dom not found",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "wv"},
                            "steps": [{"tap": {"id": "nonexistent"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert not result.ok
