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

import time
from collections.abc import Callable
from typing import Any, Protocol, cast

from bajutsu.drivers import base

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


# (playwright, browser, page) — browser/playwright are held only to tear them down in close().
Starter = Callable[[bool], "tuple[Any, Any, _Page]"]


def _start_chromium(headless: bool) -> tuple[Any, Any, _Page]:  # pragma: no cover - needs a browser
    """Start Playwright + a fresh Chromium context. A fresh context is the `erase` equivalent
    (no cookies / storage carried over). Lazily imports playwright so the default path stays free."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    # A headed run adds a small slow-motion so a human can actually follow each action; headless
    # (the default / CI) stays at full speed.
    browser = pw.chromium.launch(headless=headless, slow_mo=0 if headless else 250)
    page = browser.new_context().new_page()
    # cast bridges playwright's real Page to our structural _Page: mypy only sees the real type
    # when the web extra is installed, and a bare `# type: ignore` would be flagged unused when
    # it isn't (so it can't satisfy both environments — the cast does).
    return pw, browser, cast(_Page, page)


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
        self._pw: Any = None
        self._browser: Any = None
        if page is not None:  # test injection: no real browser
            self._page: _Page = page
        else:
            self._pw, self._browser, self._page = starter(headless)

    # --- lifecycle (web equivalents of env.Env launch/erase/terminate) ---

    def navigate(self) -> None:
        """Go to the configured base URL — the `launch` equivalent."""
        self._page.goto(self._base_url)

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    # --- Driver Protocol ---

    def query(self) -> list[base.Element]:
        records = self._page.evaluate(QUERY_JS)
        return parse_dom(records if isinstance(records, list) else [])

    def _center(self, sel: base.Selector) -> base.Point:
        x, y, w, h = base.resolve_unique(self.query(), sel)["frame"]
        return (x + w / 2, y + h / 2)

    def tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._page.mouse.click(x, y)

    def tap_point(self, p: base.Point) -> None:
        self._page.mouse.click(p[0], p[1])

    def double_tap(self, sel: base.Selector) -> None:
        x, y = self._center(sel)
        self._page.mouse.dblclick(x, y)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        x, y = self._center(sel)
        self._page.mouse.move(x, y)
        self._page.mouse.down()
        time.sleep(duration)
        self._page.mouse.up()

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._page.mouse.move(frm[0], frm[1])
        self._page.mouse.down()
        self._page.mouse.move(to[0], to[1])
        self._page.mouse.up()

    def pinch(self, sel: base.Selector, scale: float) -> None:
        raise base.UnsupportedAction("pinch は multiTouch が必要; web backend は v1 では未対応")

    def rotate(self, sel: base.Selector, radians: float) -> None:
        raise base.UnsupportedAction("rotate は multiTouch が必要; web backend は v1 では未対応")

    def type_text(self, text: str) -> None:
        # The orchestrator taps `into` before this (see _do_type), focusing the field — same
        # contract idb relies on, so typing always lands in the just-focused element.
        self._page.keyboard.type(text)

    def wait_for(self, sel: base.Selector, timeout: float) -> bool:
        return len(base.find_all(self.query(), sel)) >= 1

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path)

    def capabilities(self) -> set[str]:
        # Playwright has a genuine semantic click and native auto-waiting; multi-touch and
        # native network are deferred (see the BE for web-backend completion).
        return {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
        }
