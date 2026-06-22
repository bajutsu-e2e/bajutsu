"""Tests for the Playwright web driver — browser-free via an injected fake page.

The DOM→Element parser is pure (unit-tested directly); the driver's actions are exercised
through a fake page that records mouse/keyboard calls, so the whole module is covered without
launching Chromium.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.playwright import (
    PlaywrightDriver,
    _norm_role,
    _str_or_none,
    parse_dom,
)


def _rec(**kw: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "identifier": None,
        "role": None,
        "label": None,
        "value": None,
        "disabled": False,
        "selected": False,
        "frame": [0, 0, 0, 0],
    }
    rec.update(kw)
    return rec


# --- pure parser ---


def test_parse_dom_maps_fields() -> None:
    [el] = parse_dom(
        [
            _rec(
                identifier="auth.email",
                role="input",
                label="Email",
                value="a@b.com",
                frame=[10, 20, 40, 10],
            )
        ]
    )
    assert el["identifier"] == "auth.email"
    assert el["label"] == "Email"
    assert el["value"] == "a@b.com"
    assert "textbox" in el["traits"]  # input role normalized
    assert el["frame"] == (10.0, 20.0, 40.0, 10.0)


def test_parse_dom_disabled_and_selected_traits() -> None:
    [el] = parse_dom([_rec(role="button", disabled=True, selected=True)])
    assert base.Trait.BUTTON in el["traits"]
    assert base.Trait.NOT_ENABLED in el["traits"]
    assert base.Trait.SELECTED in el["traits"]


def test_parse_dom_blank_fields_become_none() -> None:
    [el] = parse_dom([_rec(identifier="", label="", value="", role=None)])
    assert el["identifier"] is None
    assert el["label"] is None
    assert el["value"] is None
    assert el["traits"] == []  # no role -> no trait


def test_parse_dom_skips_non_dict() -> None:
    out = parse_dom([_rec(identifier="x"), "nope"])  # type: ignore[list-item]
    assert len(out) == 1
    assert out[0]["identifier"] == "x"


def test_norm_role_and_str_or_none() -> None:
    assert _norm_role("a") == base.Trait.LINK
    assert _norm_role("custom") == "custom"  # unmapped passes through
    assert _norm_role(None) is None
    assert _str_or_none("") is None
    assert _str_or_none(None) is None
    assert _str_or_none(3) == "3"


# --- fake page + driver actions ---


class _FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def click(self, x: float, y: float) -> None:
        self.calls.append(("click", x, y))

    def dblclick(self, x: float, y: float) -> None:
        self.calls.append(("dblclick", x, y))

    def move(self, x: float, y: float) -> None:
        self.calls.append(("move", x, y))

    def down(self) -> None:
        self.calls.append(("down",))

    def up(self) -> None:
        self.calls.append(("up",))


class _FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)


class _FakePage:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.goto_url: str | None = None
        self.shot: str | None = None
        self._handlers: dict[str, list[Any]] = {}

    def evaluate(self, expression: str) -> Any:
        return list(self._records)

    def goto(self, url: str) -> object:
        self.goto_url = url
        return None

    def screenshot(self, *, path: str) -> object:
        self.shot = path
        return None

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def fire(self, event: str, arg: Any) -> None:
        for handler in self._handlers.get(event, []):
            handler(arg)


class _FakeDialog:
    def __init__(self, message: str, type: str = "alert") -> None:
        self.message = message
        self.type = type
        self.dismissed = False

    def dismiss(self) -> None:
        self.dismissed = True


class _FakeRequest:
    def __init__(self, nav: bool) -> None:
        self._nav = nav

    def is_navigation_request(self) -> bool:
        return self._nav


class _FakeFrame:
    def __init__(self, parent: object | None = None) -> None:
        self.parent_frame = parent


class _FakeResponse:
    def __init__(self, status: int, nav: bool = True, frame: _FakeFrame | None = None) -> None:
        self.status = status
        self.request = _FakeRequest(nav)
        self.frame = frame


def _driver(records: list[dict[str, Any]]) -> tuple[PlaywrightDriver, _FakePage]:
    page = _FakePage(records)
    return PlaywrightDriver("http://app.test/index.html", page=page), page


def test_query_parses_page() -> None:
    drv, _ = _driver([_rec(identifier="home.title", role="heading")])
    els = drv.query()
    assert els[0]["identifier"] == "home.title"


def test_is_a_driver() -> None:
    drv, _ = _driver([])
    assert isinstance(drv, base.Driver)
    assert drv.name == "playwright"


def test_tap_clicks_frame_center() -> None:
    drv, page = _driver([_rec(identifier="counter.increment", frame=[10, 20, 40, 10])])
    drv.tap({"id": "counter.increment"})
    assert page.mouse.calls == [("click", 30.0, 25.0)]


def test_tap_ambiguous_raises() -> None:
    drv, _ = _driver([_rec(identifier="x"), _rec(identifier="x")])
    with pytest.raises(base.AmbiguousSelector):
        drv.tap({"id": "x"})


def test_tap_not_found_raises() -> None:
    drv, _ = _driver([])
    with pytest.raises(base.ElementNotFound):
        drv.tap({"id": "missing"})


def test_tap_point() -> None:
    drv, page = _driver([])
    drv.tap_point((5.0, 6.0))
    assert page.mouse.calls == [("click", 5.0, 6.0)]


def test_double_tap() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    drv.double_tap({"id": "x"})
    assert page.mouse.calls == [("dblclick", 5.0, 5.0)]


def test_long_press() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    drv.long_press({"id": "x"}, 0.0)
    assert [c[0] for c in page.mouse.calls] == ["move", "down", "up"]


def test_swipe() -> None:
    drv, page = _driver([])
    drv.swipe((0.0, 0.0), (10.0, 0.0))
    assert [c[0] for c in page.mouse.calls] == ["move", "down", "move", "up"]


def test_type_text() -> None:
    drv, page = _driver([])
    drv.type_text("hello")
    assert page.keyboard.typed == ["hello"]


def test_wait_for() -> None:
    drv, _ = _driver([_rec(identifier="home.title")])
    assert drv.wait_for({"id": "home.title"}, 1.0) is True
    assert drv.wait_for({"id": "nope"}, 1.0) is False


def test_pinch_and_rotate_unsupported() -> None:
    drv, _ = _driver([_rec(identifier="x")])
    with pytest.raises(base.UnsupportedAction):
        drv.pinch({"id": "x"}, 2.0)
    with pytest.raises(base.UnsupportedAction):
        drv.rotate({"id": "x"}, 1.0)


def test_screenshot_and_navigate_and_close() -> None:
    drv, page = _driver([])
    drv.screenshot("/tmp/shot.png")
    assert page.shot == "/tmp/shot.png"
    drv.navigate()
    assert page.goto_url == "http://app.test/index.html"
    drv.close()  # injected page -> no browser to close; must not raise


def test_capabilities() -> None:
    drv, _ = _driver([])
    caps = drv.capabilities()
    assert base.Capability.SEMANTIC_TAP in caps
    assert base.Capability.CONDITION_WAIT in caps
    assert base.Capability.MULTI_TOUCH not in caps
    assert base.Capability.NETWORK not in caps


def test_importing_module_does_not_load_playwright() -> None:
    # The playwright package must stay off the import path until a browser is actually started.
    assert "playwright" not in sys.modules


# --- web health / dialog signals (the crawl crash-detection seam, BE-0066) ---


def test_page_errors_accumulate_and_are_consumed() -> None:
    drv, page = _driver([])
    assert drv.pop_page_errors() == []
    page.fire("pageerror", "ReferenceError: x is not defined")
    assert drv.pop_page_errors() == ["ReferenceError: x is not defined"]
    assert drv.pop_page_errors() == []  # consumed by the previous read


def test_nav_status_tracks_main_frame_navigations_only() -> None:
    drv, page = _driver([])
    assert drv.last_nav_status() is None
    page.fire("response", _FakeResponse(500, nav=True))
    assert drv.last_nav_status() == 500
    page.fire("response", _FakeResponse(200, nav=False))  # a subresource — ignored
    assert drv.last_nav_status() == 500


def test_nav_status_ignores_subframe_navigations() -> None:
    # An iframe 404 is a sub-frame navigation, not the top-level document — it must not be read
    # as the app crashing. A main-frame navigation (parent_frame is None) is still recorded.
    drv, page = _driver([])
    page.fire("response", _FakeResponse(404, nav=True, frame=_FakeFrame(parent=object())))
    assert drv.last_nav_status() is None  # iframe navigation ignored
    page.fire("response", _FakeResponse(200, nav=True, frame=_FakeFrame(parent=None)))
    assert drv.last_nav_status() == 200  # main-frame navigation recorded


def test_dialog_is_auto_dismissed_and_recorded() -> None:
    drv, page = _driver([])
    dialog = _FakeDialog("Leave page?", type="beforeunload")
    page.fire("dialog", dialog)
    assert dialog.dismissed is True  # deterministic fixed policy: dismiss, no model
    assert drv.pop_dialogs() == ["Leave page?"]
    assert drv.pop_dialogs() == []  # consumed


def test_web_is_alive_flags_each_crash_signal() -> None:
    from bajutsu.drivers.playwright import web_is_alive

    drv, page = _driver([])
    els = parse_dom([_rec(identifier="home.title")])
    assert web_is_alive(drv, els) is True  # healthy: content, no error, no bad status
    assert web_is_alive(drv, []) is False  # blank document = collapsed page
    page.fire("pageerror", "TypeError: boom")
    assert web_is_alive(drv, els) is False  # uncaught JS exception
    assert web_is_alive(drv, els) is True  # the error was consumed; back to healthy
    page.fire("response", _FakeResponse(503, nav=True))
    assert web_is_alive(drv, els) is False  # navigated to a 5xx
    page.fire("response", _FakeResponse(200, nav=True))
    assert web_is_alive(drv, els) is True  # a later good navigation clears it
