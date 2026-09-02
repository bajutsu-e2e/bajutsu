"""Tests for the Playwright web driver — browser-free via an injected fake page.

The DOM→Element parser is pure (unit-tested directly); the driver's actions are exercised
through a fake page that records mouse/keyboard calls, so the whole module is covered without
launching Chromium.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.dom import QUERY_JS, _norm_role, _str_or_none, parse_dom
from bajutsu.common.drivers.playwright import PlaywrightDriver, _Started


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
    assert "textField" in el["traits"]  # input role normalized to iOS-aligned trait
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


# --- expanded role mapping (BE-0024): interactive HTML elements → ACTIONABLE_TRAITS ---


def test_input_and_textbox_map_to_textfield() -> None:
    """input / textbox (ARIA) should become textField — the iOS-aligned trait that
    ACTIONABLE_TRAITS already includes, so doctor counts text inputs as actionable."""
    for role in ("input", "textbox"):
        [el] = parse_dom([_rec(role=role)])
        assert "textField" in el["traits"], f"role={role!r} should map to textField"


def test_textarea_maps_to_textview() -> None:
    """textarea should become textView — the trait for multi-line text areas."""
    [el] = parse_dom([_rec(role="textarea")])
    assert "textView" in el["traits"]


def test_checkbox_and_radio_map_to_switch() -> None:
    """checkbox / radio ARIA roles should become switch so doctor scores them."""
    for role in ("checkbox", "radio"):
        [el] = parse_dom([_rec(role=role)])
        assert "switch" in el["traits"], f"role={role!r} should map to switch"


def test_switch_role_maps_to_switch() -> None:
    """The ARIA switch role should map to the switch trait directly."""
    [el] = parse_dom([_rec(role="switch")])
    assert "switch" in el["traits"]


def test_select_and_combobox_and_listbox_map_to_button() -> None:
    """select / combobox / listbox are tappable controls — map to button."""
    for role in ("select", "combobox", "listbox"):
        [el] = parse_dom([_rec(role=role)])
        assert "button" in el["traits"], f"role={role!r} should map to button"


def test_slider_role_maps_to_slider() -> None:
    """The ARIA slider role should map to the slider trait."""
    [el] = parse_dom([_rec(role="slider")])
    assert "slider" in el["traits"]


def test_tab_role_maps_to_tab() -> None:
    """The ARIA tab role should map to the tab trait."""
    [el] = parse_dom([_rec(role="tab")])
    assert "tab" in el["traits"]


def test_option_and_menuitem_map_to_cell() -> None:
    """option / menuitem are selectable items — map to cell."""
    for role in ("option", "menuitem", "menuitemcheckbox", "menuitemradio"):
        [el] = parse_dom([_rec(role=role)])
        assert "cell" in el["traits"], f"role={role!r} should map to cell"


def test_spinbutton_maps_to_textfield() -> None:
    """spinbutton is a numeric text input — map to textField."""
    [el] = parse_dom([_rec(role="spinbutton")])
    assert "textField" in el["traits"]


def test_searchbox_maps_to_searchfield() -> None:
    """searchbox ARIA role should map to searchField."""
    [el] = parse_dom([_rec(role="searchbox")])
    assert "searchField" in el["traits"]


def test_password_input_carries_the_masked_input_trait_alongside_its_role() -> None:
    """BE-0331: `input[type=password]` has no ARIA role of its own, so it reaches the role map as a
    bare `input` and normalizes to textField indistinguishably from a plain one. The trait is what
    lets the default masking mean the same thing here as on iOS — and it is added *beside* the role,
    so the field stays a text input to selectors and to the crawl's INPUT_TRAITS."""
    [secure] = parse_dom([_rec(role="input", secure=True)])
    assert base.Trait.SECURE_TEXT_FIELD in secure["traits"]
    assert "textField" in secure["traits"]
    [plain] = parse_dom([_rec(role="input")])
    assert base.Trait.SECURE_TEXT_FIELD not in plain["traits"]


def test_query_js_reads_the_password_type_off_the_node() -> None:
    """The trait is only as good as the record the browser produces, and QUERY_JS is the one place
    the DOM is asked — a parser fed a `secure` key nothing ever sets would pass while every real
    page reported a plain input."""
    assert "el.type === 'password'" in QUERY_JS
    assert "secure:" in QUERY_JS


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

    def wheel(self, delta_x: float, delta_y: float) -> None:
        self.calls.append(("wheel", delta_x, delta_y))


class _FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []

    def type(self, text: str) -> None:
        self.typed.append(text)

    def press(self, key: str) -> None:
        self.pressed.append(key)


class _FakeCDP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def send(self, method: str, params: Any) -> None:
        self.calls.append((method, params))


class _FakeBrowserContext:
    def __init__(self, cdp: _FakeCDP) -> None:
        self._cdp = cdp

    def new_cdp_session(self, page: Any) -> _FakeCDP:
        return self._cdp


class _FakePage:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()
        self.goto_url: str | None = None
        self.went_back = False
        self.shot: str | None = None
        self._handlers: dict[str, list[Any]] = {}
        self.cdp = _FakeCDP()
        self.context = _FakeBrowserContext(self.cdp)
        self.evaluated: list[str] = []  # every evaluate() expression, for asserting non-query JS
        # Optional queue of return values for non-query evaluate() calls. Each pop() serves one
        # non-query call; once empty, non-query calls return None (simulating undefined JS return).
        self.evaluate_returns: list[Any] = []

    def evaluate(self, expression: str) -> Any:
        self.evaluated.append(expression)
        if expression == QUERY_JS:
            return list(self._records)
        if self.evaluate_returns:
            return self.evaluate_returns.pop(0)
        return list(self._records)

    def goto(self, url: str) -> object:
        self.goto_url = url
        return None

    def go_back(self) -> object:
        self.went_back = True
        return None

    def screenshot(self, *, path: str) -> object:
        self.shot = path
        return None

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Any) -> None:
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    def fire(self, event: str, arg: Any) -> None:
        for handler in self._handlers.get(event, []):
            handler(arg)


class _FakeConsole:
    """A Playwright ConsoleMessage stand-in (has .type and .text)."""

    def __init__(self, type: str, text: str) -> None:
        self.type = type
        self.text = text


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


# --- engine selection (BE-0076): the Starter built from `browser` picks getattr(pw, engine) ---


class _FakeEngine:
    """A Playwright engine handle (pw.chromium / pw.firefox / pw.webkit) recording its launch."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.launched: dict[str, Any] | None = None
        self.browser: _FakeBrowser | None = None  # the most recent browser launched (device tests)

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launched = kwargs
        self.browser = _FakeBrowser([_FakePage([])])
        return self.browser


class _FakeSyncPw:
    """A `sync_playwright()` stand-in exposing the three engines as attributes."""

    def __init__(self) -> None:
        self.chromium = _FakeEngine("chromium")
        self.firefox = _FakeEngine("firefox")
        self.webkit = _FakeEngine("webkit")
        self.stopped = 0
        # Playwright's device-preset registry (BE-0228): a test populates the presets it drives.
        self.devices: dict[str, dict[str, Any]] = {}

    def start(self) -> _FakeSyncPw:
        return self

    def stop(self) -> None:
        self.stopped += 1


def _patch_playwright(monkeypatch: pytest.MonkeyPatch, pw: _FakeSyncPw) -> None:
    """Make the driver's lazy `from playwright.sync_api import sync_playwright` return `pw`."""
    # Python imports the parent package before the submodule, so the `playwright` package must be in
    # sys.modules too — otherwise `from playwright.sync_api import …` raises ModuleNotFoundError in
    # the fast gate, where the web extra (and the real playwright package) isn't installed.
    monkeypatch.setitem(sys.modules, "playwright", type(sys)("playwright"))
    sync_api = type(sys)("playwright.sync_api")
    sync_api.sync_playwright = lambda: pw  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
def test_start_browser_launches_the_named_engine(
    monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    # _start_browser(engine) reaches the engine via getattr(pw, engine), so firefox/webkit launch
    # the same way Chromium did — the one generalization Phase 1 needs.
    from bajutsu.common.drivers.playwright import _start_browser

    pw = _FakeSyncPw()
    _patch_playwright(monkeypatch, pw)
    _start_browser(engine)(True)
    assert getattr(pw, engine).launched == {"headless": True, "slow_mo": 0}


def test_driver_browser_arg_selects_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # PlaywrightDriver(..., browser="firefox") builds its starter for that engine, so the real
    # browser process is Firefox — proving make_driver's `browser=` reaches the launch.
    pw = _FakeSyncPw()
    _patch_playwright(monkeypatch, pw)
    PlaywrightDriver("http://app.test/", browser="firefox")
    assert pw.firefox.launched is not None
    assert pw.chromium.launched is None and pw.webkit.launched is None


def test_driver_browser_defaults_to_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    pw = _FakeSyncPw()
    _patch_playwright(monkeypatch, pw)
    PlaywrightDriver("http://app.test/")
    assert pw.chromium.launched is not None
    assert pw.firefox.launched is None


def test_relaunch_rebuilds_the_same_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # relaunch() re-invokes the engine starter, so a wedged webkit lane comes back as webkit, not
    # silently as chromium.
    pw = _FakeSyncPw()
    _patch_playwright(monkeypatch, pw)
    drv = PlaywrightDriver("http://app.test/", browser="webkit")
    pw.webkit.launched = None  # reset to observe only the relaunch
    drv.relaunch()
    assert pw.webkit.launched is not None


# --- device mode (BE-0228): a context is created with a Playwright device preset's emulation ---

_IPHONE_13 = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 3,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": "Mozilla/5.0 (iPhone; ...)",
}


def test_device_context_kwargs_desktop_is_empty() -> None:
    # "desktop" is a plain context — no emulation — so the mapping is empty and playwright.devices is
    # never consulted (the default path stays free).
    from bajutsu.common.drivers.playwright import _device_context_kwargs

    class _NoDevices:
        @property
        def devices(self) -> dict[str, Any]:
            raise AssertionError("desktop must not consult playwright.devices")

    assert _device_context_kwargs(_NoDevices(), "desktop") == {}


def test_device_context_kwargs_resolves_a_preset() -> None:
    # A preset name expands to its Playwright descriptor, ready to spread into new_context.
    from bajutsu.common.drivers.playwright import _device_context_kwargs

    pw = _FakeSyncPw()
    pw.devices["iPhone 13"] = dict(_IPHONE_13)
    assert _device_context_kwargs(pw, "iPhone 13") == _IPHONE_13


def test_device_context_kwargs_unknown_preset_raises() -> None:
    # An unknown preset fails loudly (at driver start), not silently as the desktop layout.
    from bajutsu.common.drivers.playwright import _device_context_kwargs

    with pytest.raises(ValueError, match="unknown deviceMode"):
        _device_context_kwargs(_FakeSyncPw(), "iPhone 999")


def test_driver_device_mode_emulates_the_initial_context(monkeypatch: pytest.MonkeyPatch) -> None:
    # PlaywrightDriver(..., device_mode="iPhone 13") builds its starter for that preset, so the very
    # first context is created with the mobile viewport/touch alongside the reduced_motion lever.
    pw = _FakeSyncPw()
    pw.devices["iPhone 13"] = dict(_IPHONE_13)
    _patch_playwright(monkeypatch, pw)
    PlaywrightDriver("http://app.test/", device_mode="iPhone 13")
    assert pw.chromium.browser is not None
    kwargs = pw.chromium.browser.context_kwargs[0]
    assert kwargs["reduced_motion"] == "reduce"
    assert kwargs["is_mobile"] is True and kwargs["has_touch"] is True


def test_driver_desktop_adds_no_device_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default (desktop) context carries only the reduced_motion lever — unchanged from before.
    pw = _FakeSyncPw()
    _patch_playwright(monkeypatch, pw)
    PlaywrightDriver("http://app.test/")
    assert pw.chromium.browser is not None
    assert pw.chromium.browser.context_kwargs[0] == {"reduced_motion": "reduce"}


def test_reset_context_carries_device_mode() -> None:
    # The web `erase` (reset_context) opens a fresh context; it must re-apply the device emulation so
    # a crawl's clean start keeps driving the mobile face, not silently fall back to desktop.
    pw = _FakePw()
    pw.devices["iPhone 13"] = dict(_IPHONE_13)
    initial_ctx = _FakeContext(_FakePage([]))
    fresh = _FakePage([])
    browser = _FakeBrowser([fresh])
    drv = PlaywrightDriver(
        "http://app.test/",
        device_mode="iPhone 13",
        starter=lambda _h: _Started(pw, browser, initial_ctx, initial_ctx.page),
    )

    drv.reset_context()

    assert browser.context_kwargs[-1]["is_mobile"] is True
    assert browser.context_kwargs[-1]["reduced_motion"] == "reduce"


def test_device_mode_descriptor_is_resolved_once() -> None:
    # The descriptor is fixed data, so it is resolved against playwright.devices once and cached: a
    # later context reuses the memoized value rather than re-resolving. Proven by deleting the preset
    # after the first resolution — a re-resolution would raise "unknown deviceMode"; the memo doesn't.
    pw = _FakePw()
    pw.devices["iPhone 13"] = dict(_IPHONE_13)
    initial_ctx = _FakeContext(_FakePage([]))
    browser = _FakeBrowser([_FakePage([]), _FakePage([])])  # two fresh contexts to open
    drv = PlaywrightDriver(
        "http://app.test/",
        device_mode="iPhone 13",
        starter=lambda _h: _Started(pw, browser, initial_ctx, initial_ctx.page),
    )

    drv.reset_context()  # first _new_context: resolves + caches the descriptor
    del pw.devices["iPhone 13"]  # a re-resolution from here on would fail
    drv.reset_context()  # second _new_context: must reuse the cache, not re-resolve

    assert browser.context_kwargs[-1]["is_mobile"] is True


def test_relaunch_keeps_device_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # relaunch() re-invokes the device-aware starter, so a recovered lane comes back emulating the
    # same phone rather than silently as desktop (BE-0077 recovery + BE-0228 stability).
    pw = _FakeSyncPw()
    pw.devices["iPhone 13"] = dict(_IPHONE_13)
    _patch_playwright(monkeypatch, pw)
    drv = PlaywrightDriver("http://app.test/", device_mode="iPhone 13")
    drv.relaunch()
    assert pw.chromium.browser is not None  # the relaunch's fresh browser
    assert pw.chromium.browser.context_kwargs[0]["is_mobile"] is True


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
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.tap({"id": "counter.increment"})
    assert page.mouse.calls == [("click", 30.0, 25.0)]


def test_tap_ambiguous_raises() -> None:
    # Distinct frames: two different on-page elements sharing a duplicate DOM id, not a
    # content-identical duplicate (which resolve_unique now collapses rather than flags ambiguous).
    drv, _ = _driver(
        [
            _rec(identifier="x", frame=[0, 0, 10, 10]),
            _rec(identifier="x", frame=[0, 20, 10, 10]),
        ]
    )
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
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.double_tap({"id": "x"})
    assert page.mouse.calls == [("dblclick", 5.0, 5.0)]


def test_tap_raises_element_not_tappable_when_covered() -> None:
    # elementFromPoint's hit chain never reaches the resolved target: an unrelated element covers
    # its point, so `tap` fails with the dedicated error rather than clicking through it.
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": False, "cover": None, "rect": None}]
    with pytest.raises(base.ElementNotTappable, match="covered by another element"):
        drv.tap({"id": "x"})
    assert page.mouse.calls == []  # never clicked


def test_tap_raises_element_not_tappable_naming_the_cover() -> None:
    # Mirrors `base.raise_if_covered`'s message shape on the other backends: the covering element's
    # identifier (or tag/DOM id) and rect are named, not just "covered by another element" with no
    # way to tell a sticky header from a toast from a modal backdrop.
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [
        {"ok": False, "cover": "modal-backdrop", "rect": [0.0, 0.0, 400.0, 800.0]}
    ]
    with pytest.raises(
        base.ElementNotTappable,
        match=r"covered by another element \('modal-backdrop' at \(0\.0, 0\.0, 400\.0, 800\.0\)\)",
    ):
        drv.tap({"id": "x"})


def test_double_tap_raises_element_not_tappable_when_covered() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": False, "cover": None, "rect": None}]
    with pytest.raises(base.ElementNotTappable):
        drv.double_tap({"id": "x"})
    assert page.mouse.calls == []


def test_long_press_raises_element_not_tappable_when_covered() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": False, "cover": None, "rect": None}]
    with pytest.raises(base.ElementNotTappable):
        drv.long_press({"id": "x"}, 0.0)
    assert page.mouse.calls == []


def test_tap_clicks_when_the_point_hits_the_element() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.tap({"id": "x"})
    assert page.mouse.calls == [("click", 5.0, 5.0)]


def test_is_tappable_true_when_the_point_hits_the_element() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    assert drv.is_tappable({"id": "x"}) is True


def test_is_tappable_false_when_covered() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": False, "cover": "div#overlay", "rect": [0.0, 0.0, 10.0, 10.0]}]
    assert drv.is_tappable({"id": "x"}) is False


def test_is_tappable_false_when_not_found() -> None:
    drv, _ = _driver([])
    assert drv.is_tappable({"id": "missing"}) is False


def test_is_tappable_propagates_ambiguous_selector() -> None:
    drv, _ = _driver(
        [
            _rec(identifier="x", frame=[0, 0, 10, 10]),
            _rec(identifier="x", frame=[0, 20, 10, 10]),
        ]
    )
    with pytest.raises(base.AmbiguousSelector):
        drv.is_tappable({"id": "x"})


def test_is_tappable_never_clicks() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.is_tappable({"id": "x"})
    assert page.mouse.calls == []


def test_is_tappable_checks_element_from_point_at_the_frame_center() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[10, 20, 40, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.is_tappable({"id": "x"})
    assert "elementFromPoint(30.0, 25.0)" in page.evaluated[-1]


def test_is_tappable_guards_against_a_below_viewport_point_before_hit_testing() -> None:
    # `elementFromPoint` returns null off-viewport regardless of occlusion (real-browser behavior
    # pinned by test_driver_conformance_web.py's below-the-fold test); this only checks that the
    # emitted JS carries the viewport guard ahead of the `elementFromPoint` call, since the fake
    # page returns a canned value rather than evaluating real JavaScript.
    drv, page = _driver([_rec(identifier="x", frame=[10, 20, 40, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.is_tappable({"id": "x"})
    script = page.evaluated[-1]
    assert "window.innerWidth" in script and "window.innerHeight" in script


def test_is_tappable_fallback_requires_a_name_match_not_just_a_rect_match() -> None:
    # The no-`data-testid` fallback (real-browser behavior pinned by
    # test_driver_conformance_web.py's identically-sized-cover test) must not accept a same-rect
    # ancestor on rect alone: this only checks that the emitted JS carries the element's own
    # accessible name (`label`) into the fallback's match condition, since the fake page returns a
    # canned value rather than evaluating real JavaScript.
    drv, page = _driver([_rec(identifier=None, label="Submit", frame=[10, 20, 40, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.is_tappable({"label": "Submit"})
    script = page.evaluated[-1]
    assert '"Submit"' in script
    assert "sameName" in script and "sameRect" in script
    assert script.index("window.innerHeight") < script.index("elementFromPoint")


def test_back_goes_back_in_history() -> None:
    # The web's `back` is browser history (BE-0210), the platform peer of Android's system back key.
    drv, page = _driver([])
    drv.back()
    assert page.went_back is True


def test_long_press() -> None:
    drv, page = _driver([_rec(identifier="x", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}]
    drv.long_press({"id": "x"}, 0.0)
    assert [c[0] for c in page.mouse.calls] == ["move", "down", "up"]


def test_swipe_is_a_literal_pointer_drag_on_a_desktop_context() -> None:
    # On a desktop context the coordinate `swipe` form and the `drag` action are a raw mouse drag
    # (canvas / map pan / resize handle) — distinct from the directional "scroll" form, which wheels.
    drv, page = _driver([])
    drv.swipe((0.0, 0.0), (10.0, 0.0))
    assert [c[0] for c in page.mouse.calls] == ["move", "down", "move", "up"]


def test_swipe_touch_drags_under_a_touch_context() -> None:
    # On a touch context (BE-0228) a pointer drag (coordinate swipe / the drag action) must be a real
    # touch drag — the pinch/rotate CDP path — so a touch-bound handle (a slider thumb, a resize
    # divider) responds; a synthesized mouse drag fires no touch listeners (BE-0227).
    drv, page = _driver([])
    drv._device_kwargs = {"has_touch": True}  # emulate a context created for touch input
    drv.swipe((5.0, 40.0), (5.0, 10.0))
    assert page.mouse.calls == []  # no mouse drag on a touch context
    types = [p["type"] for _, p in page.cdp.calls]
    assert types[0] == "touchStart" and types[-1] == "touchEnd"
    start = _touch_points(page.cdp.calls[0][1])
    last_move = _touch_points(page.cdp.calls[-2][1])  # final touchMove before touchEnd
    assert len(start) == 1 and start[0] == (5.0, 40.0)  # one finger, beginning at frm
    assert last_move[0] == (5.0, 10.0)  # travelling to `to` (the finger follows the drag)


def test_scroll_wheels_on_a_desktop_context() -> None:
    # A directional scroll on the default (desktop) context is a wheel over the gesture's start, since
    # a mouse drag does not scroll a page (BE-0227). The wheel delta is the reverse of the travel: an
    # up swipe (frm below to) scrolls the page down (positive delta_y).
    drv, page = _driver([])
    drv.scroll((5.0, 40.0), (5.0, 10.0))
    assert page.mouse.calls == [("move", 5.0, 40.0), ("wheel", 0.0, 30.0)]


def test_scroll_delta_signs_match_each_direction() -> None:
    # frm - to is the wheel delta: down/right (frm before to) go negative, up/left positive.
    drv, page = _driver([])
    drv.scroll((10.0, 10.0), (10.0, 40.0))  # a down swipe
    drv.scroll((10.0, 10.0), (40.0, 10.0))  # a right swipe
    wheels = [c for c in page.mouse.calls if c[0] == "wheel"]
    assert wheels == [("wheel", 0.0, -30.0), ("wheel", -30.0, 0.0)]


def test_scroll_touch_drags_under_a_touch_context() -> None:
    # A touch context (BE-0228) scrolls with a real single-finger touch drag — the pinch/rotate path
    # (CDP touch events), not a wheel — so the page's touch/scroll listeners fire (BE-0227).
    drv, page = _driver([])
    drv._device_kwargs = {"has_touch": True}  # emulate a context created for touch input
    drv.scroll((5.0, 40.0), (5.0, 10.0))
    assert page.mouse.calls == []  # no wheel / mouse drag on a touch context
    types = [p["type"] for _, p in page.cdp.calls]
    assert types[0] == "touchStart" and types[-1] == "touchEnd"
    start = _touch_points(page.cdp.calls[0][1])
    last_move = _touch_points(page.cdp.calls[-2][1])  # final touchMove before touchEnd
    assert len(start) == 1 and start[0] == (5.0, 40.0)  # one finger, beginning at frm
    assert last_move[0] == (5.0, 10.0)  # travelling to `to` (the finger follows the drag)


def test_type_text() -> None:
    drv, page = _driver([])
    drv.type_text("hello")
    assert page.keyboard.typed == ["hello"]


def test_delete_text_presses_backspace_count_times() -> None:
    # `count` Backspace presses on the focused field — Playwright has no repeat-count (BE-0265).
    drv, page = _driver([])
    drv.delete_text(3)
    assert page.keyboard.pressed == ["Backspace", "Backspace", "Backspace"]


def test_select_all_presses_control_a() -> None:
    drv, page = _driver([])
    drv.select_all()
    assert page.keyboard.pressed == ["Control+a"]


def test_copy_selection_presses_control_c() -> None:
    drv, page = _driver([])
    drv.copy_selection()
    assert page.keyboard.pressed == ["Control+c"]


def test_wait_for_is_single_shot() -> None:
    # BE-0118: single-shot check of the current DOM; the deadline poll lives in base.wait_until.
    drv, _ = _driver([_rec(identifier="home.title")])
    assert drv.wait_for({"id": "home.title"}) is True
    assert drv.wait_for({"id": "nope"}) is False


def test_select_option_sets_value_at_resolved_point() -> None:
    # The <select> resolves through the determinism core (unique match); the driver then locates it
    # at the frame center — the same coordinate a click would use — and sets the value there,
    # so matching never leaves resolve_unique for Playwright's own engine (BE-0191).
    drv, page = _driver(
        [_rec(identifier="nav.theme-picker", role="select", frame=[10, 20, 40, 10])]
    )
    # First return serves `_center_checked`'s own occlusion check (`_point_hits`); second is the
    # select JS's own result.
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}, "ok"]
    drv.select_option({"id": "nav.theme-picker"}, "midnight")
    # The last evaluate() is the select JS; it reads the resolved center (30, 25) via
    # elementFromPoint and assigns the requested option value.
    select_js = page.evaluated[-1]
    assert "elementFromPoint(30.0, 25.0)" in select_js
    assert '"midnight"' in select_js
    assert "new Event('change'" in select_js


def test_select_option_ambiguous_raises() -> None:
    # Distinct frames: two different <select>s sharing a duplicate DOM id, not a content-identical
    # duplicate (which resolve_unique now collapses rather than flags ambiguous).
    drv, _ = _driver(
        [
            _rec(identifier="dup", role="select", frame=[0, 0, 10, 10]),
            _rec(identifier="dup", role="select", frame=[0, 20, 10, 10]),
        ]
    )
    with pytest.raises(base.AmbiguousSelector):
        drv.select_option({"id": "dup"}, "midnight")


def test_select_option_raises_element_not_found_when_not_a_select() -> None:
    # When JS returns 'no-select' (the resolved element is not a <select>), the driver raises
    # ElementNotFound rather than letting _wedge_guard turn it into a DeviceError crash. First
    # return serves the occlusion check (`_center_checked`); the <select> itself is not covered.
    drv, page = _driver([_rec(identifier="nav.theme-picker", role="select", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}, "no-select"]
    with pytest.raises(base.ElementNotFound, match="not a <select>"):
        drv.select_option({"id": "nav.theme-picker"}, "midnight")


def test_select_option_raises_element_not_found_when_no_matching_option() -> None:
    # When JS returns 'no-option' (no <option> has the requested value), the driver raises
    # ElementNotFound — the same taxonomy as a missing element, not a browser crash.
    drv, page = _driver([_rec(identifier="nav.theme-picker", role="select", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": True, "cover": None, "rect": None}, "no-option"]
    with pytest.raises(base.ElementNotFound, match="no option with value"):
        drv.select_option({"id": "nav.theme-picker"}, "midnight")


def test_select_option_raises_element_not_tappable_when_covered() -> None:
    # An overlay covering the <select> makes `elementFromPoint` resolve to the overlay: the old
    # unchecked path would then see `closest('select')` come back null and raise the factually
    # wrong "not a <select>". Checking via `_center_checked` first raises `ElementNotTappable`
    # naming the actual cover instead, before the select JS ever runs.
    drv, page = _driver([_rec(identifier="nav.theme-picker", role="select", frame=[0, 0, 10, 10])])
    page.evaluate_returns = [{"ok": False, "cover": "div#overlay", "rect": [0.0, 0.0, 10.0, 10.0]}]
    with pytest.raises(base.ElementNotTappable, match="covered by another element"):
        drv.select_option({"id": "nav.theme-picker"}, "midnight")
    # The select JS itself never ran: only the occlusion check's own evaluate() was consumed.
    assert len(page.evaluate_returns) == 0


def _touch_points(params: Any) -> list[tuple[float, float]]:
    return [(p["x"], p["y"]) for p in params["touchPoints"]]


def test_pinch_dispatches_two_diverging_touch_points() -> None:
    # An element centered at (50,50); a scale>1 pinch ends with the two fingers farther apart.
    drv, page = _driver([_rec(identifier="img", frame=[0, 0, 100, 100])])
    drv.pinch({"id": "img"}, 2.0)
    events = [m for m, _ in page.cdp.calls]
    assert events[0] == "Input.dispatchTouchEvent"
    types = [p["type"] for _, p in page.cdp.calls]
    assert types[0] == "touchStart" and types[-1] == "touchEnd"
    start = _touch_points(page.cdp.calls[0][1])
    last_move = _touch_points(page.cdp.calls[-2][1])  # final touchMove before touchEnd
    assert len(start) == 2  # two fingers
    start_span = abs(start[1][0] - start[0][0])
    end_span = abs(last_move[1][0] - last_move[0][0])
    assert end_span > start_span  # scale 2.0 spreads the fingers apart


def test_pinch_in_converges_touch_points() -> None:
    drv, page = _driver([_rec(identifier="img", frame=[0, 0, 100, 100])])
    drv.pinch({"id": "img"}, 0.5)
    start = _touch_points(page.cdp.calls[0][1])
    last_move = _touch_points(page.cdp.calls[-2][1])
    assert abs(last_move[1][0] - last_move[0][0]) < abs(start[1][0] - start[0][0])


def test_rotate_moves_points_off_the_starting_axis() -> None:
    # The fingers start on a horizontal axis; a rotation gives them a vertical component.
    drv, page = _driver([_rec(identifier="img", frame=[0, 0, 100, 100])])
    drv.rotate({"id": "img"}, 1.0)  # ~57°
    start = _touch_points(page.cdp.calls[0][1])
    last_move = _touch_points(page.cdp.calls[-2][1])
    assert start[0][1] == start[1][1]  # started level (same y)
    assert last_move[0][1] != last_move[1][1]  # rotated out of the horizontal


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
    assert base.Capability.NETWORK in caps  # native observe + stub (BE-0054)
    assert (
        base.Capability.MULTI_TOUCH in caps
    )  # two-finger gestures via CDP touch synthesis (BE-0054)
    assert base.Capability.TEXT_SELECTION in caps  # Ctrl+A / Ctrl+C actuate (BE-0280)


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
    from bajutsu.common.drivers.playwright import web_is_alive

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


# --- parallel crawl: fresh-context reset, browser relaunch, wedge → DeviceError (BE-0077) ---


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page
        self.closed = 0

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed += 1


class _FakeBrowser:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = list(pages)  # one handed out per new_context().new_page()
        self.contexts: list[_FakeContext] = []
        self.context_kwargs: list[dict[str, Any]] = []  # kwargs each new_context() was opened with
        self.closed = 0

    def new_context(self, **kwargs: Any) -> _FakeContext:
        self.context_kwargs.append(kwargs)
        ctx = _FakeContext(self._pages.pop(0))
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        self.closed += 1


class _FakePw:
    def __init__(self) -> None:
        self.stopped = 0
        # Playwright's device-preset registry (BE-0228); a device-mode test populates what it drives.
        self.devices: dict[str, dict[str, Any]] = {}

    def stop(self) -> None:
        self.stopped += 1


def test_reset_context_opens_a_fresh_context_closing_the_old_one() -> None:
    """The web `erase` (BE-0077): reset_context discards the current context, opens a fresh one, and
    navigates the new page — clean cookies / storage per frontier visit, one context alive at a
    time (no leak), with the health handlers rebound to the fresh page."""
    pw, initial_ctx = _FakePw(), _FakeContext(_FakePage([]))
    fresh = _FakePage([])
    browser = _FakeBrowser([fresh])  # hands `fresh` to the next new_context().new_page()
    drv = PlaywrightDriver(
        "http://app.test/index.html",
        starter=lambda _h: _Started(pw, browser, initial_ctx, initial_ctx.page),
    )

    drv.reset_context()

    assert initial_ctx.closed == 1  # the prior context was discarded, not leaked
    assert len(browser.contexts) == 1  # exactly one fresh context opened
    assert fresh.goto_url == "http://app.test/index.html"  # navigated on the fresh page
    fresh.fire("pageerror", "boom")  # health handlers rebound to the fresh page
    assert drv.pop_page_errors() == ["boom"]
    # The determinism lever (BE-0191 unit 5): every context collapses the app's CSS motion to instant.
    assert browser.context_kwargs[-1].get("reduced_motion") == "reduce"


def test_starter_context_carries_reduced_motion() -> None:
    """The real `_start_browser` passes reduced_motion="reduce" to new_context() (BE-0191 unit 5).

    The inner `start` function imports Playwright at call time, so a live browser is never needed to
    inspect its source. A source-level assertion mirrors how tests/serve/test_theme_tokens.py regexes
    CSS/JS: it fails if the line in playwright.py is reverted, which a fake-starter that independently
    re-implements the behavior would not catch.
    """
    import inspect

    # _start_browser(engine) returns the inner `start` closure; Playwright is imported lazily inside
    # it (only when start(headless) is actually called), so this is safe in the fast suite.
    import re

    from bajutsu.common.drivers import playwright as pw_module

    start_fn = pw_module._start_browser("chromium")
    src = inspect.getsource(start_fn)
    # Regex on the semantic content so a harmless quote-style / spacing reformat doesn't break the
    # test — only an actual removal of reduced_motion from new_context() would cause failure.
    assert re.search(r'reduced_motion\s*=\s*[\'"]reduce[\'"]', src), (
        '_start_browser start() must pass reduced_motion="reduce" to new_context() '
        "(the BE-0191 unit 5 determinism lever — revert new_context(reduced_motion=...) in playwright.py's start() to see this fail)"
    )


def test_relaunch_replaces_the_browser_process() -> None:
    """relaunch tears down the wedged browser + Playwright and starts a fresh process (BE-0077), then
    rebinds the health handlers to the new page so the recovered lane keeps reporting crash signals."""
    old_pw, old_browser, old_page = _FakePw(), _FakeBrowser([]), _FakePage([])
    new_pw, new_browser, new_page = _FakePw(), _FakeBrowser([]), _FakePage([])
    starts = iter(
        [
            _Started(old_pw, old_browser, _FakeContext(old_page), old_page),  # initial
            _Started(new_pw, new_browser, _FakeContext(new_page), new_page),  # the relaunch
        ]
    )
    drv = PlaywrightDriver("http://app.test/", starter=lambda _h: next(starts))

    drv.relaunch()

    assert old_browser.closed == 1 and old_pw.stopped == 1  # the wedged process was torn down
    new_page.fire("pageerror", "boom")  # the fresh page's signals now feed the driver
    assert drv.pop_page_errors() == ["boom"]


def test_relaunch_stops_playwright_even_if_browser_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged browser's close() usually raises (it's already dead). Teardown is per-handle, so the
    Playwright process (`pw`) is still stopped rather than leaked across relaunches."""

    class _PwError(Exception):
        pass

    monkeypatch.setattr(sys.modules["bajutsu.common.drivers.playwright"], "_PW_ERRORS", (_PwError,))

    class _DeadBrowser(_FakeBrowser):
        def close(self) -> None:
            raise _PwError("Target page, context or browser has been closed")

    old_pw, dead_browser = _FakePw(), _DeadBrowser([])
    new_pw, new_browser, new_page = _FakePw(), _FakeBrowser([]), _FakePage([])
    starts = iter(
        [
            _Started(old_pw, dead_browser, _FakeContext(_FakePage([])), _FakePage([])),
            _Started(new_pw, new_browser, _FakeContext(new_page), new_page),
        ]
    )
    drv = PlaywrightDriver("http://app.test/", starter=lambda _h: next(starts))

    drv.relaunch()

    assert old_pw.stopped == 1  # pw stopped despite the browser's close() raising — no process leak
    new_page.fire("pageerror", "boom")
    assert drv.pop_page_errors() == ["boom"]  # the fresh process was still adopted


def test_close_stops_playwright_even_if_browser_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE-0342: the pool's end-of-lease teardown now escalates anything `close()` doesn't suppress
    into a run-failing wiring defect — so a browser that already died (the same "expected, not a
    defect" case `relaunch` above already suppresses) must not raise here either, and `pw` must
    still be stopped rather than leaked."""

    class _PwError(Exception):
        pass

    monkeypatch.setattr(sys.modules["bajutsu.common.drivers.playwright"], "_PW_ERRORS", (_PwError,))

    class _DeadBrowser(_FakeBrowser):
        def close(self) -> None:
            raise _PwError("Target page, context or browser has been closed")

    pw, dead_browser = _FakePw(), _DeadBrowser([])
    drv = PlaywrightDriver(
        "http://app.test/",
        starter=lambda _h: _Started(pw, dead_browser, _FakeContext(_FakePage([])), _FakePage([])),
    )

    drv.close()  # must not raise despite the browser's close() raising

    assert pw.stopped == 1  # pw stopped despite the browser's close() raising — no process leak


def test_close_is_idempotent_after_clearing_its_handles() -> None:
    """BE-0342: `launch_driver`'s failure path can call `close()` a second time — every backend's
    teardown must now tolerate that. A second `_pw.stop()` on an already-stopped Playwright is a
    driver-connection error, not a `playwright.sync_api.Error`, so it would escape `close()`'s own
    suppress and fail the run — unless the first call already cleared the handles, the same reason
    `relaunch` above clears them before starting fresh."""

    class _OnceOnlyPw(_FakePw):
        def stop(self) -> None:
            if self.stopped:
                raise RuntimeError("driver connection already closed")
            super().stop()

    pw = _OnceOnlyPw()
    drv = PlaywrightDriver(
        "http://app.test/",
        starter=lambda _h: _Started(
            pw, _FakeBrowser([]), _FakeContext(_FakePage([])), _FakePage([])
        ),
    )

    drv.close()
    drv.close()  # must not raise — the first call already cleared the handles

    assert pw.stopped == 1  # stopped exactly once, never retried against an already-stopped process


def test_relaunch_is_a_noop_for_an_injected_page() -> None:
    drv, _ = _driver([])  # an injected test page has no real browser to relaunch
    drv.relaunch()  # must not raise


def test_reset_context_on_an_injected_page_just_navigates() -> None:
    """With no real browser (an injected page), reset_context has no context to swap — it falls back
    to re-navigating, the path the crawl uses when a page is injected for tests."""
    drv, page = _driver([])
    drv.reset_context()
    assert page.goto_url == "http://app.test/index.html"


def test_wedge_surfaces_as_device_error_but_selection_errors_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wedged browser raises a Playwright error from a page op; the driver re-raises it as the
    crawl's recoverable `simctl.DeviceError` (BE-0077), so a pool worker relaunches instead of the crawl
    aborting. A selection failure is not a wedge and still propagates unchanged."""
    from bajutsu import simctl

    class _PwError(Exception):
        pass

    # Playwright isn't installed in the gate env, so stand in its error types (the cached module
    # global). Patch via sys.modules to keep this file's single `from bajutsu.common.drivers.playwright`
    # import style (no second `import … as` of the same module).
    monkeypatch.setattr(sys.modules["bajutsu.common.drivers.playwright"], "_PW_ERRORS", (_PwError,))

    class _WedgedPage(_FakePage):
        def evaluate(self, expression: str) -> Any:
            raise _PwError("Target page, context or browser has been closed")

    wedged = PlaywrightDriver("http://app.test/", page=_WedgedPage([]))
    with pytest.raises(simctl.DeviceError):
        wedged.query()
    with pytest.raises(simctl.DeviceError):
        wedged.tap({"id": "anything"})  # tap → _center → query() wedges → DeviceError

    # A missing selector is a SelectorError, not a wedge — it must not be masked as a DeviceError.
    healthy = PlaywrightDriver("http://app.test/", page=_FakePage([]))
    with pytest.raises(base.ElementNotFound):
        healthy.tap({"id": "missing"})


def test_driver_interval_captures_console_and_pageerror(tmp_path: Any) -> None:
    # The web `deviceLog` evidence kind streams the browser console + uncaught page errors.
    drv, page = _driver([])
    path = tmp_path / "device.log"
    interval = drv.driver_interval("deviceLog", path)
    assert interval is not None
    assert interval.kind == "deviceLog"
    assert interval.provider == "playwright"

    page.fire("console", _FakeConsole("log", "hello"))
    page.fire("console", _FakeConsole("error", "oops"))
    page.fire("pageerror", "boom")
    interval.stop()

    text = path.read_text(encoding="utf-8")
    assert "[log] hello" in text
    assert "[error] oops" in text
    assert "[pageerror] boom" in text


def test_driver_interval_stop_detaches_handlers(tmp_path: Any) -> None:
    # After stop(), further console events must not be written (the listener is removed).
    drv, page = _driver([])
    path = tmp_path / "device.log"
    interval = drv.driver_interval("deviceLog", path)
    assert interval is not None
    interval.stop()
    page.fire("console", _FakeConsole("log", "after-stop"))
    assert "after-stop" not in path.read_text(encoding="utf-8")


def test_driver_interval_unknown_kind_is_none(tmp_path: Any) -> None:
    drv, _ = _driver([])
    assert drv.driver_interval("appTrace", tmp_path / "appTrace.raw") is None


# --- video evidence (BE-0054) ---


class _FakeVideo:
    def __init__(self, src: Any) -> None:
        self._src = src

    def path(self) -> str:
        return str(self._src)


class _FakeVideoPage(_FakePage):
    def __init__(self, records: list[dict[str, Any]], video: _FakeVideo) -> None:
        super().__init__(records)
        self.video = video


class _VideoContext:
    def __init__(self, page: Any, record_video_dir: Any = None) -> None:
        self.record_video_dir = record_video_dir
        self._page = page
        self.closed = False

    def new_page(self) -> Any:
        return self._page

    def close(self) -> None:
        self.closed = True


class _VideoBrowser:
    def __init__(self, page: Any) -> None:
        self._page = page
        self.contexts: list[_VideoContext] = []

    def new_context(self, **kwargs: Any) -> _VideoContext:
        ctx = _VideoContext(self._page, kwargs.get("record_video_dir"))
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        return None


class _VideoPw:
    def stop(self) -> None:
        return None


def _video_driver(video_dir: Any, src: Any) -> tuple[PlaywrightDriver, _VideoBrowser]:
    page = _FakeVideoPage([], _FakeVideo(src))
    browser = _VideoBrowser(page)
    pw = _FakePw()

    def starter(_h: bool) -> _Started:
        return _Started(pw, browser, _FakeContext(page), page)

    drv = PlaywrightDriver("http://app.test/", record_video_dir=video_dir, starter=starter)
    return drv, browser


def test_web_driver_records_video_when_dir_set(tmp_path: Any) -> None:
    src = tmp_path / "raw.webm"
    src.write_bytes(b"vid")
    _, browser = _video_driver(tmp_path / "vtmp", src)
    # The live context was (re)created with record_video_dir so Playwright records it.
    assert browser.contexts[-1].record_video_dir == str(tmp_path / "vtmp")


def test_driver_interval_video_finalizes_to_target(tmp_path: Any) -> None:
    src = tmp_path / "raw.webm"
    src.write_bytes(b"vid")
    target = tmp_path / "out" / "scenario.mp4"
    drv, browser = _video_driver(tmp_path / "vtmp", src)
    interval = drv.driver_interval("video", target)
    assert interval is not None
    assert interval.kind == "video"
    assert interval.provider == "playwright"
    interval.stop()
    assert browser.contexts[-1].closed  # context closed to finalize the recording
    assert target.read_bytes() == b"vid"  # the saved video moved to the artifact path
    assert not src.exists()


def test_driver_interval_video_carries_true_start(tmp_path: Any) -> None:
    # The web actuator needs no confirmation poll: page-creation latency is negligible next to a
    # subprocess spawn's, so stamping right after new_page() (where the video itself first exists)
    # is accurate enough.
    src = tmp_path / "raw.webm"
    src.write_bytes(b"vid")
    drv, _ = _video_driver(tmp_path / "vtmp", src)
    interval = drv.driver_interval("video", tmp_path / "out" / "scenario.mp4")
    assert interval is not None
    assert isinstance(interval.true_start, float)


def test_driver_interval_video_none_without_recording(tmp_path: Any) -> None:
    # No record_video_dir on this lane (video not requested): no video interval.
    drv, _ = _driver([])
    assert drv.driver_interval("video", tmp_path / "scenario.mp4") is None
