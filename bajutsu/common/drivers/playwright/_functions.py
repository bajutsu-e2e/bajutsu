"""Start a browser, normalize the DOM into elements, and synthesize gestures on the page."""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from bajutsu.common.backend_cli import simctl
from bajutsu.common.drivers import base
from bajutsu.common.drivers.dom import QUERY_JS, parse_dom

from ._frame_scope import _FrameScope
from ._page import _Page
from ._shared import Starter
from ._started import _Started

if TYPE_CHECKING:
    from .playwright_driver import PlaywrightDriver


# Playwright's error types, imported lazily and cached so the heavy dep stays off the default path
# (an empty tuple when the web extra isn't installed — there is then no real browser to wedge).
# `Error` is the base of every Playwright browser-side failure (TimeoutError is one of its
# subclasses); the crawl deliberately treats the whole family as recoverable lane faults — a Tier-1
# discovery tool isolates and relaunches a bad lane rather than aborting, and the worker's
# retire-after-N counter bounds the cost if a lane never heals. Narrowing to specific subclasses
# would risk an unlisted wedge type aborting the crawl instead.
_PW_ERRORS: tuple[type[BaseException], ...] | None = None


def _rotate_point(p: base.Point, center: base.Point, radians: float) -> base.Point:
    """Rotate point `p` about `center` by `radians` (for two-finger rotate synthesis)."""
    dx, dy = p[0] - center[0], p[1] - center[1]
    cos, sin = math.cos(radians), math.sin(radians)
    return (center[0] + dx * cos - dy * sin, center[1] + dx * sin + dy * cos)


def _device_context_kwargs(pw: Any, device_mode: str) -> dict[str, Any]:
    """Resolve a web target's device mode (BE-0228) to `new_context` kwargs.

    "desktop" (the default) is a plain context with no emulation, so the mapping is empty and today's
    behaviour is unchanged — `playwright.devices` is never consulted. Any other value is a Playwright
    device preset name whose descriptor (viewport / device_scale_factor / is_mobile / has_touch /
    user_agent) spreads straight into `new_context`. An unknown name fails loudly here, at driver
    start before any scenario step runs, rather than silently driving the desktop layout.
    """
    if device_mode == "desktop":
        return {}
    try:
        descriptor = pw.devices[device_mode]
    except KeyError:
        # Only the lookup is guarded: a KeyError raised while *copying* the descriptor below would be
        # a real fault, not a bad name, and must not be mislabelled as an unknown preset.
        raise ValueError(
            f"unknown runsOn.emulate {device_mode!r}: use 'desktop' or a Playwright device preset name "
            "(e.g. 'iPhone 13'); see playwright.devices for the full list"
        ) from None
    return dict(descriptor)


def _start_browser(
    engine: str, device_mode: str = "desktop"
) -> Starter:  # pragma: no cover - needs a browser
    """A `Starter` that launches the named Playwright engine (chromium / firefox / webkit, BE-0076).

    Each engine is reached the same way — `getattr(pw, engine)` — so firefox / webkit launch through
    the identical path that was hard-wired to Chromium. A fresh context is the `erase` equivalent (no
    cookies / storage carried over). `device_mode` emulates a phone (BE-0228); desktop is unchanged.
    Playwright is imported lazily so the default path stays free.
    """

    def start(headless: bool) -> _Started:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        # A headed run adds a small slow-motion so a human can actually follow each action; headless
        # (the default / CI) stays at full speed.
        browser = getattr(pw, engine).launch(headless=headless, slow_mo=0 if headless else 250)
        # reduced_motion="reduce" is the determinism lever (BE-0191 unit 5): CSS transitions the app
        # under test may run (e.g. the serve UI's own themable motion, dogfooded in demos/serve-ui/)
        # collapse to instant, so condition-wait assertions never race an animation and an element is
        # never briefly duplicated mid-transition. Motion is a look, never part of the verdict. The
        # device descriptor (BE-0228) rides alongside it, empty for the desktop default.
        context = browser.new_context(
            reduced_motion="reduce", **_device_context_kwargs(pw, device_mode)
        )
        page = context.new_page()
        # cast bridges playwright's real Page to our structural _Page: mypy only sees the real type
        # when the web extra is installed, and a bare `# type: ignore` would be flagged unused when
        # it isn't (so it can't satisfy both environments — the cast does).
        return _Started(pw, browser, context, cast(_Page, page))

    return start


def _playwright_error_types() -> tuple[type[BaseException], ...]:
    global _PW_ERRORS  # noqa: PLW0603  # memoizes the optional import once per process
    if _PW_ERRORS is None:
        try:
            from playwright.sync_api import Error
            from playwright.sync_api import TimeoutError as _Timeout

            _PW_ERRORS = (Error, _Timeout)
        except ImportError:
            _PW_ERRORS = ()
    return _PW_ERRORS


# An `<iframe>`'s content box — where its document's (0, 0) sits — in its owner document's viewport,
# or null when the iframe is not rendered. Border and padding are inside the bounding rect but outside
# the child viewport, so both are stepped over.
_IFRAME_CONTENT_BOX_JS = """
(el) => {
  const style = getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return null;
  const r = el.getBoundingClientRect();
  const pl = parseFloat(style.paddingLeft) || 0, pt = parseFloat(style.paddingTop) || 0;
  const pr = parseFloat(style.paddingRight) || 0, pb = parseFloat(style.paddingBottom) || 0;
  return [r.x + el.clientLeft + pl, r.y + el.clientTop + pt,
          el.clientWidth - pl - pr, el.clientHeight - pt - pb];
}
"""


def _intersect(a: base.Frame, b: base.Frame | None) -> base.Frame | None:
    """The overlap of two rects, `a` itself when `b` is unbounded, or None when they do not meet."""
    if b is None:
        return a
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if right < x or bottom < y:
        return None
    return (x, y, right - x, bottom - y)


def _iframe_elements(page: _Page) -> list[tuple[base.Element, _FrameScope]]:
    """Every iframe's elements (nested ones included), framed in main-page CSS pixels.

    Walks Playwright's frame tree rather than `contentDocument` from page JS, so a cross-origin
    iframe — out of reach of the parent's script — is read too. An element scrolled or clipped
    outside every enclosing iframe's box is not on screen and is left out; one outside the main
    viewport is kept, the same as a main-document element (BE-0326). A frame that detaches or
    navigates mid-snapshot is skipped: its content is not observable in this snapshot, and the next
    condition-wait poll reads it again. An injected page without a frame tree yields nothing.
    """
    main = getattr(page, "main_frame", None)
    if main is None:
        return []
    out: list[tuple[base.Element, _FrameScope]] = []

    def walk(
        frame: Any,
        origin: base.Point,
        clip: base.Frame | None,
        hosts: tuple[tuple[Any, base.Point], ...],
    ) -> None:
        for child in frame.child_frames:
            try:
                host = child.frame_element()
                box = host.evaluate(_IFRAME_CONTENT_BOX_JS)
                if not box or box[2] <= 0 or box[3] <= 0:
                    continue
                child_origin = (origin[0] + box[0], origin[1] + box[1])
                child_clip = _intersect((*child_origin, box[2], box[3]), clip)
                if child_clip is None:
                    continue
                records = child.evaluate(QUERY_JS)
            except _playwright_error_types():
                continue
            scope = _FrameScope(child, child_origin, (*hosts, (host, origin)))
            for el in parse_dom(records if isinstance(records, list) else []):
                x, y, w, h = el["frame"]
                shifted = (x + child_origin[0], y + child_origin[1], w, h)
                if _intersect(shifted, child_clip) is None:
                    continue
                el["frame"] = shifted
                out.append((el, scope))
            walk(child, child_origin, child_clip, scope.hosts)

    walk(main, (0.0, 0.0), None, ())
    return out


def _wedge_guard[F: Callable[..., Any]](method: F) -> F:
    """Turn a browser-side failure into the crawl's recoverable "lane wedged" signal (BE-0077).

    A renderer crash, a hung page, a navigation timeout — any Playwright error from a page operation —
    re-raises as `simctl.DeviceError`, which a pool worker isolates (handing its frontier entry back and
    relaunching the browser) instead of sinking the crawl. Selection failures (`base.SelectorError`)
    and an obstructed-target failure (`base.ElementNotTappable`) are not wedges and pass through
    unchanged, as do real bugs (any non-Playwright exception).
    """

    @functools.wraps(method)
    def wrapper(self: PlaywrightDriver, *args: Any, **kwargs: Any) -> Any:
        try:
            return method(self, *args, **kwargs)
        except (base.SelectorError, base.ElementNotTappable):
            raise
        except Exception as exc:
            if isinstance(exc, _playwright_error_types()):
                raise simctl.DeviceError(f"web browser fault (recoverable wedge): {exc}") from exc
            raise

    return cast(F, wrapper)


def _is_browser_fault(exc: BaseException, target: str) -> bool:
    """Whether *exc* says the page is gone or crashed — a browser fault, not a bad URL (BE-0464).

    Playwright raises `TargetClosedError` for a closed page, but exports the class only from its
    private `_impl` package, so it is matched by name across the error's class hierarchy. A renderer
    crash has no class of its own: its message ("Page crashed", "...because page crashed!") is the
    only signal, read with *target* removed so a URL path like `/page-crashed` cannot fake one.
    """
    if any(cls.__name__ == "TargetClosedError" for cls in type(exc).__mro__):
        return True
    return "page crashed" in str(exc).replace(target, "").lower()


def _nav_reason(exc: BaseException, target: str) -> str:
    """The first meaningful line of a `goto` error, with the URL left out (BE-0464).

    The URL can carry a substituted `${secrets.*}` value, and Playwright may echo it percent-encoded
    where value redaction cannot match it, so the reason drops it the way the `http` step reports only
    a transport failure's reason.
    """
    lines = [line for line in str(exc).splitlines() if line.strip()]
    if not lines:
        return type(exc).__name__
    first = lines[0].replace(target, "<url>")
    return re.sub(r"\s+at\s+\S+\s*$", "", first)


def web_is_alive(driver: PlaywrightDriver, elements: list[base.Element]) -> bool:
    """The web crash signal for the crawl (BE-0066).

    False on an uncaught JS exception, a 4xx/5xx main-frame navigation, or a blank document. All
    three are machine facts (an event fired, a status number, an empty element set), so prime
    directive #1 holds — AI stays out of the verdict. This is the web counterpart of the iOS
    accessibility-tree `is_app_alive`.
    """
    if driver.pop_page_errors():
        return False
    status = driver.last_nav_status()
    if status is not None and status >= 400:
        return False
    return bool(elements)
