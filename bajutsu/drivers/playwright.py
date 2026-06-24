"""Web backend (Playwright, Chromium — headless by default, headed on request).

Walks the DOM into normalized Elements and acts by coordinate-clicking the resolved
frame center — the *same* path idb uses. The browser has a native semantic click
(`get_by_test_id().click()`), but using it would route matching through Playwright's
own engine and diverge from the determinism core; instead every action resolves through
the shared `base.resolve_unique` / `find_all` against a `query()` snapshot, so a scenario
behaves identically on web and iOS.

The id convention is `data-testid` (developer-set, non-localized) → `Selector.id`; ARIA
`role` (or the tag) → `traits`; accessible name / `aria-label` / text → `label`.

`playwright` is imported lazily (only when a browser is actually started), so importing
this module — or the default CLI path — never pulls in the heavy dependency.
"""

from __future__ import annotations

import contextlib
import functools
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, cast

from bajutsu import env
from bajutsu.drivers import base

if TYPE_CHECKING:
    from bajutsu.scenario.models.mocks import Mock
    from bajutsu.web_network import WebNetworkCollector

# One DOM walk: visible, interactive / a11y-relevant nodes → records the parser maps to Elements.
QUERY_JS = """
() => {
  const out = [];
  const sel = '[data-testid], button, a, input, select, textarea, [role]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    if (r.width === 0 && r.height === 0) continue;
    const text = (el.innerText || el.textContent || '').trim();
    out.push({
      identifier: el.getAttribute('data-testid'),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      label: el.getAttribute('aria-label') || (text ? text.slice(0, 200) : null),
      value: ('value' in el) ? el.value : null,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      selected: el.getAttribute('aria-selected') === 'true'
                || el.getAttribute('aria-checked') === 'true',
      frame: [r.x, r.y, r.width, r.height],
    });
  }
  return out;
}
"""

# HTML tags / ARIA roles → the normalized trait tokens state assertions look for.
_ROLE_MAP = {
    "a": base.Trait.LINK,
    "link": base.Trait.LINK,
    "button": base.Trait.BUTTON,
    "input": "textbox",
    "textbox": "textbox",
    "textarea": "textbox",
}


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return str(v)


def _norm_role(role: str | None) -> str | None:
    if not role:
        return None
    return _ROLE_MAP.get(role, role)


def _to_element(rec: dict[str, Any]) -> base.Element:
    traits: list[str] = []
    role = _norm_role(_str_or_none(rec.get("role")))
    if role:
        traits.append(role)
    if rec.get("disabled"):
        traits.append(base.Trait.NOT_ENABLED)
    if rec.get("selected"):
        traits.append(base.Trait.SELECTED)
    f = rec.get("frame") or [0, 0, 0, 0]
    return {
        "identifier": _str_or_none(rec.get("identifier")),
        "label": _str_or_none(rec.get("label")),
        "value": _str_or_none(rec.get("value")),
        "traits": traits,
        "frame": (float(f[0]), float(f[1]), float(f[2]), float(f[3])),
    }


def parse_dom(records: list[dict[str, Any]]) -> list[base.Element]:
    """Map the QUERY_JS records to normalized Elements (the browser-free, unit-tested core)."""
    return [_to_element(r) for r in records if isinstance(r, dict)]


# The subset of Playwright's Page the driver uses — kept as a Protocol so tests can inject a
# fake page without importing playwright, and the real (untyped, lazily imported) page satisfies it.
class _Mouse(Protocol):
    def click(self, x: float, y: float) -> None:
        pass

    def dblclick(self, x: float, y: float) -> None:
        pass

    def move(self, x: float, y: float) -> None:
        pass

    def down(self) -> None:
        pass

    def up(self) -> None:
        pass


class _Keyboard(Protocol):
    def type(self, text: str) -> None:
        pass


class _Page(Protocol):
    mouse: _Mouse
    keyboard: _Keyboard

    def evaluate(self, expression: str) -> Any:
        pass

    def goto(self, url: str) -> object:
        pass

    def screenshot(self, *, path: str) -> object:
        pass

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        pass


class _Started(NamedTuple):
    """What a `Starter` returns. `browser` / `pw` are held to tear down in close(); `context` is held
    so a per-visit reset can close it before opening a fresh one (BE-0077), bounding live contexts to
    one per worker rather than leaking one per frontier visit. The three handles are `Any` because
    playwright is imported lazily — its types aren't in scope at module load — and naming the slots
    here (vs. a bare 4-tuple) keeps the adjacent `browser` / `context` handles from being transposed."""

    pw: Any
    browser: Any
    context: Any
    page: _Page


Starter = Callable[[bool], _Started]


def _start_chromium(headless: bool) -> _Started:  # pragma: no cover - needs a browser
    """Start Playwright + a fresh Chromium context. A fresh context is the `erase` equivalent
    (no cookies / storage carried over). Lazily imports playwright so the default path stays free."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    # A headed run adds a small slow-motion so a human can actually follow each action; headless
    # (the default / CI) stays at full speed.
    browser = pw.chromium.launch(headless=headless, slow_mo=0 if headless else 250)
    context = browser.new_context()
    page = context.new_page()
    # cast bridges playwright's real Page to our structural _Page: mypy only sees the real type
    # when the web extra is installed, and a bare `# type: ignore` would be flagged unused when
    # it isn't (so it can't satisfy both environments — the cast does).
    return _Started(pw, browser, context, cast(_Page, page))


# Playwright's error types, imported lazily and cached so the heavy dep stays off the default path
# (an empty tuple when the web extra isn't installed — there is then no real browser to wedge).
# `Error` is the base of every Playwright browser-side failure (TimeoutError is one of its
# subclasses); the crawl deliberately treats the whole family as recoverable lane faults — a Tier-1
# discovery tool isolates and relaunches a bad lane rather than aborting, and the worker's
# retire-after-N counter bounds the cost if a lane never heals. Narrowing to specific subclasses
# would risk an unlisted wedge type aborting the crawl instead.
_PW_ERRORS: tuple[type[BaseException], ...] | None = None


def _playwright_error_types() -> tuple[type[BaseException], ...]:
    global _PW_ERRORS
    if _PW_ERRORS is None:
        try:
            from playwright.sync_api import Error
            from playwright.sync_api import TimeoutError as _Timeout

            _PW_ERRORS = (Error, _Timeout)
        except ImportError:
            _PW_ERRORS = ()
    return _PW_ERRORS


def _wedge_guard[F: Callable[..., Any]](method: F) -> F:
    """Turn a browser-side failure into the crawl's recoverable "lane wedged" signal (BE-0077). A
    renderer crash, a hung page, a navigation timeout — any Playwright error from a page operation —
    re-raises as `env.DeviceError`, which a pool worker isolates (handing its frontier entry back and
    relaunching the browser) instead of sinking the crawl. Selection failures (`base.SelectorError`)
    are not wedges and pass through unchanged, as do real bugs (any non-Playwright exception)."""

    @functools.wraps(method)
    def wrapper(self: PlaywrightDriver, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except base.SelectorError:
            raise
        except Exception as exc:
            if isinstance(exc, _playwright_error_types()):
                raise env.DeviceError(f"web browser fault (recoverable wedge): {exc}") from exc
            raise

    return cast(F, wrapper)


def web_is_alive(driver: PlaywrightDriver, elements: list[base.Element]) -> bool:
    """The web crash signal for the crawl (BE-0066): False on an uncaught JS exception, a 4xx/5xx
    main-frame navigation, or a blank document. All three are machine facts (an event fired, a
    status number, an empty element set), so prime directive #1 holds — AI stays out of the
    verdict. This is the web counterpart of the iOS accessibility-tree `is_app_alive`."""
    if driver.pop_page_errors():
        return False
    status = driver.last_nav_status()
    if status is not None and status >= 400:
        return False
    return bool(elements)


class PlaywrightDriver:
    name = "playwright"

    def __init__(
        self,
        base_url: str,
        *,
        headless: bool = True,
        page: _Page | None = None,
        starter: Starter = _start_chromium,
    ) -> None:
        self._base_url = base_url
        # Kept so a wedged browser can be relaunched in place (BE-0077): the same starter + headless
        # mode build the replacement process.
        self._headless = headless
        self._starter = starter
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None  # current BrowserContext (web); closed + replaced on each reset
        # Deterministic web health / dialog signals the crawl reads (BE-0066): an uncaught JS
        # exception, a 4xx/5xx main-frame navigation, and a JS dialog are all machine facts — no
        # model is consulted. A JS dialog blocks the page until handled, so it is auto-dismissed by
        # a fixed policy and merely recorded. `_bind` clears these buffers and registers the
        # handlers for the current page (a fresh context or a relaunched browser rebinds the same).
        self._page_errors: list[str]
        self._last_nav_status: int | None
        self._dialogs: list[str]
        self._page: _Page
        if page is None:  # not a test injection: start a real browser process
            self._pw, self._browser, self._context, page = starter(headless)
        self._bind(page)

    def _bind(self, page: _Page) -> None:
        """Adopt a freshly created page (a new context, or a relaunched browser) as the live page:
        clear the consuming health buffers and (re)register the dialog / health handlers on it, so
        the new page starts with a clean signal slate."""
        self._page = page
        self._page_errors = []
        self._last_nav_status = None
        self._dialogs = []
        self._register_health_handlers()

    def _register_health_handlers(self) -> None:
        on = getattr(self._page, "on", None)
        if on is None:  # a minimal injected page without event support — skip silently
            return
        on("pageerror", self._on_pageerror)
        on("response", self._on_response)
        on("dialog", self._on_dialog)

    def _on_pageerror(self, error: Any) -> None:
        self._page_errors.append(str(error))

    def _on_response(self, response: Any) -> None:
        # Only the top-level (main-frame) navigation's status signals a "navigated to an error";
        # subresource responses (images, XHR) and sub-frame (iframe) navigations are noise — an
        # iframe 404 must not be read as the app crashing. Gate to the main frame when Playwright
        # exposes frame info; a minimal injected fake without it keeps the navigation-request check.
        if not response.request.is_navigation_request():
            return
        frame = getattr(response, "frame", None)
        if frame is not None and getattr(frame, "parent_frame", None) is not None:
            return  # a sub-frame navigation, not the top-level document
        self._last_nav_status = int(response.status)

    def _on_dialog(self, dialog: Any) -> None:
        self._dialogs.append(str(dialog.message))
        dialog.dismiss()  # fixed, non-destructive policy (alert→close, confirm→cancel, stay)

    def pop_page_errors(self) -> list[str]:
        """Uncaught JS exceptions seen since the last read (consuming)."""
        errors, self._page_errors = self._page_errors, []
        return errors

    def last_nav_status(self) -> int | None:
        """HTTP status of the most recent main-frame navigation, or None if none yet."""
        return self._last_nav_status

    def pop_dialogs(self) -> list[str]:
        """Messages of JS dialogs auto-handled since the last read (consuming)."""
        dialogs, self._dialogs = self._dialogs, []
        return dialogs

    # --- lifecycle (web equivalents of env.Env launch/erase/terminate) ---

    @_wedge_guard
    def navigate(self) -> None:
        """Go to the configured base URL — the `launch` equivalent."""
        self._page.goto(self._base_url)

    @_wedge_guard
    def reset_context(self) -> None:
        """The crawl's clean start (the `erase` equivalent): open a fresh BrowserContext + page —
        no cookies / storage / history carried across frontier visits — then navigate to the base
        URL. Cheap (no browser-process restart), and it lets a `path_to` recorded in one worker's
        browser replay from the same clean state in another (BE-0077). An injected test page has no
        contexts, so it just re-navigates."""
        if self._browser is not None:
            # Discard the current context (its cookies / storage / history) and open a clean one, so
            # at most one context is alive per worker — the per-visit `erase`, not a slow leak.
            if self._context is not None:
                with contextlib.suppress(*_playwright_error_types()):
                    self._context.close()
            self._context = self._browser.new_context()
            self._bind(self._context.new_page())
        self.navigate()

    def relaunch(self) -> None:
        """Tear down a wedged browser process and start a fresh one (BE-0077 fault isolation).
        Unlike `reset_context` (a cheap fresh context inside the same browser), this discards the
        whole browser — the unit the crawl hard-kills when a renderer crashes, a page hangs, or a
        navigation times out; the worker's next reset re-navigates the fresh process. A no-op for
        an injected test page (no real browser to relaunch)."""
        if self._browser is None:  # injected test page — nothing to relaunch
            return
        # Best-effort teardown of the faulted browser before replacing it, each handle on its own:
        # the browser may already be dead (target closed) so closing it can raise — but the Playwright
        # process (`pw`) must still be stopped or it leaks across relaunches, so suppress per handle
        # rather than around one combined close(). Clear the references, then start fresh. The loud
        # failure path is the *new* browser failing to start below, which propagates (a real fault).
        pw_errors = _playwright_error_types()
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer is not None:
                with contextlib.suppress(*pw_errors):
                    closer()
        self._pw = self._browser = self._context = None
        self._pw, self._browser, self._context, page = self._starter(self._headless)
        self._bind(page)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    # --- Driver Protocol ---

    @_wedge_guard
    def query(self) -> list[base.Element]:
        records = self._page.evaluate(QUERY_JS)
        return parse_dom(records if isinstance(records, list) else [])

    def _center(self, sel: base.Selector) -> base.Point:
        x, y, w, h = base.resolve_unique(self.query(), sel)["frame"]
        return (x + w / 2, y + h / 2)

    @_wedge_guard
    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._page.mouse.click(x, y)

    @_wedge_guard
    def tap_point(self, p: base.Point) -> None:
        self._page.mouse.click(p[0], p[1])

    @_wedge_guard
    def double_tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._page.mouse.dblclick(x, y)

    @_wedge_guard
    def long_press(self, sel: base.Selector, duration: float) -> None:
        x, y = self._center(sel)
        self._page.mouse.move(x, y)
        self._page.mouse.down()
        time.sleep(duration)
        self._page.mouse.up()

    @_wedge_guard
    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._page.mouse.move(frm[0], frm[1])
        self._page.mouse.down()
        self._page.mouse.move(to[0], to[1])
        self._page.mouse.up()

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction("pinch は multiTouch が必要; web backend は v1 では未対応")

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction("rotate は multiTouch が必要; web backend は v1 では未対応")

    @_wedge_guard
    def type_text(self, text: str) -> None:
        # The orchestrator taps `into` before this (see _do_type), focusing the field — same
        # contract idb relies on, so typing always lands in the just-focused element.
        self._page.keyboard.type(text)

    @_wedge_guard
    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.query(), sel)) >= 1

    @_wedge_guard
    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path)

    def network_collector(self, mocks: list[Mock] | None = None) -> WebNetworkCollector:
        """A collector hooked to this driver's page (Playwright sees every request natively), so
        the run loop's `request` assertion + network evidence work on web (BE-0054). `mocks` are
        fulfilled in-process via `page.route`. Imported lazily to keep the page private."""
        from bajutsu.web_network import WebNetworkCollector

        return WebNetworkCollector(self._page, mocks)

    # Playwright has a genuine semantic click, native auto-waiting, and native network observation +
    # stubbing (BE-0054); multi-touch is still deferred. Class constant so the preflight (BE-0082)
    # reads it via `backends.capabilities_for` without starting a browser.
    CAPABILITIES = frozenset(
        {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.NETWORK,
        }
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)
