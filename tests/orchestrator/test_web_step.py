"""Tests for the web (WebView context) step in the run loop."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.webview import WebViewTransportError
from bajutsu.common.orchestrator import run_scenario


class FakeBridge:
    """A fake bridge for run-loop tests: returns canned DOM elements and records taps."""

    def __init__(self, dom_elements: list[base.Element]) -> None:
        self._elements = dom_elements
        self.tapped: list[tuple[str, base.Point]] = []

    def session(self, webview_id: str, frame: base.Frame) -> AbstractContextManager[None]:
        return nullcontext()

    def query_dom(self, webview_id: str) -> list[base.Element]:
        return self._elements

    def tap_element(self, webview_id: str, point: base.Point) -> None:
        self.tapped.append((webview_id, point))

    def type_text(self, webview_id: str, text: str) -> None:
        pass

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        pass


def test_web_step_taps_dom_element() -> None:
    native_screen = [el("checkout.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        {
            "identifier": "place-order",
            "label": "Place Order",
            "traits": ["button"],
            "value": None,
            "frame": (50.0, 100.0, 200.0, 40.0),
            "nativeZ": None,
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


def test_web_step_within_resolution_undeclared_interruption_still_fails_the_step() -> None:
    # `base.resolve_unique(active_driver.query(), host_sel)` resolves `within` on the *native*
    # driver before the block ever switches to the WebView one — the same native-only surface an
    # interruption can reach, even though the tap that follows on the DOM driver succeeds (BE-0406
    # Unit 2b).
    native_screen = [el("checkout.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        {
            "identifier": "place-order",
            "label": "Place Order",
            "traits": ["button"],
            "value": None,
            "frame": (50.0, 100.0, 200.0, 40.0),
            "nativeZ": None,
        },
    ]
    bridge = FakeBridge(dom_elements)
    driver = FakeDriver(native_screen)
    driver.interruptions_declined_to_drain = [["Save", "Not Now"]]
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
    assert result.ok is False
    assert len(bridge.tapped) == 1  # the DOM tap itself still succeeded
    assert result.failure is not None
    assert "undeclared system alert" in result.failure
    assert "Save" in result.failure
    assert "Not Now" in result.failure


def test_web_step_assert_inside_web_context() -> None:
    native_screen = [el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))]
    dom_elements: list[base.Element] = [
        {
            "identifier": "confirmation",
            "label": "Order placed",
            "traits": [],
            "value": None,
            "frame": (10.0, 10.0, 100.0, 20.0),
            "nativeZ": None,
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


def test_native_step_after_web_step_runs_on_native_driver() -> None:
    """The active driver is restored after a web block: a following native step taps the device."""
    native_screen = [
        el("app.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0)),
        el("done", "Done", ["button"]),
    ]
    dom_elements: list[base.Element] = [
        {
            "identifier": "place-order",
            "label": "Place Order",
            "traits": ["button"],
            "value": None,
            "frame": (50.0, 100.0, 200.0, 40.0),
            "nativeZ": None,
        },
    ]
    bridge = FakeBridge(dom_elements)
    driver = FakeDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "web then native",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [{"tap": {"id": "place-order"}}],
                        },
                    },
                    {"tap": {"id": "done"}},
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    # The web tap went through the bridge; the following native tap went to the device driver,
    # proving the active driver was restored after the web block.
    assert len(bridge.tapped) == 1
    assert [a[1] for a in driver.actions if a[0] == "tap"] == [{"id": "done"}]


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
    assert "no match" in (result.failure or "")


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
    failure = result.failure or ""
    assert "no WebView source" in failure
    # Android leaves the variable unset by design (BE-0482), so the reason must not point at it.
    assert "BAJUTSU_WEBVIEW_PORT" not in failure


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


class SessionBridge(FakeBridge):
    """Records each block's session: the id and frame it opened with, and whether it closed."""

    def __init__(
        self,
        dom_elements: list[base.Element],
        *,
        open_error: Exception | None = None,
        query_error: Exception | None = None,
    ) -> None:
        super().__init__(dom_elements)
        self.opened: list[tuple[str, base.Frame]] = []
        self.closed = 0
        self._open_error = open_error
        self._query_error = query_error

    @contextmanager
    def _session(self, webview_id: str, frame: base.Frame) -> Iterator[None]:
        if self._open_error is not None:
            raise self._open_error
        self.opened.append((webview_id, frame))
        try:
            yield
        finally:
            self.closed += 1

    def session(self, webview_id: str, frame: base.Frame) -> AbstractContextManager[None]:
        return self._session(webview_id, frame)

    def query_dom(self, webview_id: str) -> list[base.Element]:
        if self._query_error is not None:
            raise self._query_error
        return super().query_dom(webview_id)


_BUTTON: base.Element = {
    "identifier": "go",
    "label": "Go",
    "traits": ["button"],
    "value": None,
    "frame": (10.0, 10.0, 50.0, 20.0),
    "nativeZ": None,
}


def _web_block(
    inner: list[dict[str, object]], after: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "name": "web session",
        "steps": [{"web": {"within": {"id": "wv"}, "steps": inner}}, *(after or [])],
    }


def test_web_session_receives_the_frame_the_within_selector_resolved() -> None:
    bridge = SessionBridge([_BUTTON])
    driver = FakeDriver([el("wv", frame=(0.0, 220.0, 1080.0, 1700.0))])
    result = run_scenario(
        driver,
        _scenario(_web_block([{"tap": {"id": "go"}}])),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.opened == [("wv", (0.0, 220.0, 1080.0, 1700.0))]
    assert bridge.closed == 1


def test_web_session_closes_when_the_block_fails() -> None:
    bridge = SessionBridge([_BUTTON])
    driver = FakeDriver([el("wv", frame=(0.0, 0.0, 400.0, 800.0))])
    result = run_scenario(
        driver,
        _scenario(_web_block([{"tap": {"id": "missing"}}])),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert not result.ok
    assert bridge.closed == 1


def test_a_missing_socket_fails_its_block_with_the_reason_and_not_the_run() -> None:
    bridge = SessionBridge(
        [_BUTTON], open_error=WebViewTransportError("no WebView DevTools socket in com.example")
    )
    driver = FakeDriver([el("wv", frame=(0.0, 0.0, 400.0, 800.0))])
    result = run_scenario(
        driver,
        _scenario(_web_block([{"tap": {"id": "go"}}])),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert not result.ok
    assert "web: no WebView DevTools socket in com.example" in (result.failure or "")


def test_a_transport_failure_inside_the_block_fails_it_and_closes_the_session() -> None:
    bridge = SessionBridge(
        [_BUTTON], query_error=WebViewTransportError("WebView DevTools socket closed: navigation")
    )
    driver = FakeDriver([el("wv", frame=(0.0, 0.0, 400.0, 800.0))])
    result = run_scenario(
        driver,
        _scenario(_web_block([{"tap": {"id": "go"}}])),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert not result.ok
    assert "socket closed" in (result.failure or "")
    assert bridge.closed == 1
