"""Web backend (Playwright, Chromium — headless by default, headed on request).

Walks the DOM into normalized Elements and acts by coordinate-clicking the resolved
frame center — the same coordinate path the device backends take. The browser has a native semantic click
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
import json
import math
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, cast

from bajutsu import simctl
from bajutsu.dom import QUERY_JS, parse_dom
from bajutsu.drivers import base
from bajutsu.drivers.actuation import Actuation, ActuationLog, Drained
from bajutsu.evidence import intervals

if TYPE_CHECKING:
    from bajutsu.scenario.models.mocks import Mock
    from bajutsu.web_network import WebNetworkCollector


def _rotate_point(p: base.Point, center: base.Point, radians: float) -> base.Point:
    """Rotate point `p` about `center` by `radians` (for two-finger rotate synthesis)."""
    dx, dy = p[0] - center[0], p[1] - center[1]
    cos, sin = math.cos(radians), math.sin(radians)
    return (center[0] + dx * cos - dy * sin, center[1] + dx * sin + dy * cos)


class _HitResult(NamedTuple):
    """`_point_hits`'s verdict: whether the point hit `el`, and what covered it if not.

    `cover` / `rect` name the covering element the same way `base.raise_if_covered` names a cover on
    the other backends; both are `None` together on a hit, or when nothing rendered at the point at
    all.
    """

    ok: bool
    cover: str | None
    rect: base.Frame | None


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

    def wheel(self, delta_x: float, delta_y: float) -> None:
        pass


class _Keyboard(Protocol):
    def type(self, text: str) -> None:
        pass

    def press(self, key: str) -> None:
        pass


class _Page(Protocol):
    mouse: _Mouse
    keyboard: _Keyboard

    def evaluate(self, expression: str) -> Any:
        pass

    def goto(self, url: str) -> object:
        pass

    def go_back(self) -> object:
        pass

    def screenshot(self, *, path: str) -> object:
        pass

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        pass


class _Started(NamedTuple):
    """What a `Starter` returns.

    `browser` / `pw` are held to tear down in close(); `context` is held
    so a per-visit reset can close it before opening a fresh one (BE-0077), bounding live contexts to
    one per worker rather than leaking one per frontier visit. The three handles are `Any` because
    playwright is imported lazily — its types aren't in scope at module load — and naming the slots
    here (vs. a bare 4-tuple) keeps the adjacent `browser` / `context` handles from being transposed.
    """

    pw: Any
    browser: Any
    context: Any
    page: _Page


Starter = Callable[[bool], _Started]


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
            f"unknown deviceMode {device_mode!r}: use 'desktop' or a Playwright device preset name "
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


# Playwright's error types, imported lazily and cached so the heavy dep stays off the default path
# (an empty tuple when the web extra isn't installed — there is then no real browser to wedge).
# `Error` is the base of every Playwright browser-side failure (TimeoutError is one of its
# subclasses); the crawl deliberately treats the whole family as recoverable lane faults — a Tier-1
# discovery tool isolates and relaunches a bad lane rather than aborting, and the worker's
# retire-after-N counter bounds the cost if a lane never heals. Narrowing to specific subclasses
# would risk an unlisted wedge type aborting the crawl instead.
_PW_ERRORS: tuple[type[BaseException], ...] | None = None

# `getBoundingClientRect` reports CSS pixels, so that is the space stamped on this backend's actuation
# records — a coordinate only means something alongside its unit.
_UNIT = "cssPixel"


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


class PlaywrightDriver:
    """Driver implementation for the web via Playwright."""

    name = "playwright"

    def __init__(
        self,
        base_url: str,
        *,
        headless: bool = True,
        browser: str = "chromium",
        device_mode: str = "desktop",
        page: _Page | None = None,
        starter: Starter | None = None,
        record_video_dir: Path | None = None,
    ) -> None:
        self._base_url = base_url
        # Kept so a wedged browser can be relaunched in place (BE-0077): the same starter + headless
        # mode build the replacement process. An explicit `starter` (the test seam) wins; otherwise
        # build one for the requested engine so relaunch() rebuilds the *same* engine (BE-0076) and
        # the *same* device emulation (BE-0228).
        self._headless = headless
        # The device mode (BE-0228) every context is created with; resolved against the live pw's
        # `devices` registry, cached so reset/relaunch re-apply the identical descriptor.
        self._device_mode = device_mode
        self._device_kwargs: dict[str, Any] | None = None
        self._starter = starter if starter is not None else _start_browser(browser, device_mode)
        # When set, contexts are created with Playwright's record_video_dir so the whole scenario is
        # filmed (BE-0054); the `video` interval finalizes and collects it. None = no recording.
        self._record_video_dir = record_video_dir
        # time.monotonic() when the current recording context was created, for the runner to anchor
        # step/network report timestamps to instead of scenario_start; None until a recording
        # context exists. Context-creation latency is negligible next to a subprocess spawn's, so
        # this needs no confirmation poll (unlike the simctl/adb interval providers).
        self._video_true_start: float | None = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None  # current BrowserContext (web); closed + replaced on each reset
        # What this driver actually actuated, drained per step by the run loop.
        self._actuations = ActuationLog()
        self._cdp: Any = None  # lazily-opened CDP session for multi-touch synthesis
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
            self._pw, self._browser, self._context, page = self._starter(headless)
            # The starter's context has no recording; if a video dir is configured, swap it for a
            # recording context so the very first scenario is filmed too.
            if self._record_video_dir is not None and self._browser is not None:
                with contextlib.suppress(*_playwright_error_types()):
                    self._context.close()
                self._context = self._new_context()
                page = self._context.new_page()
                self._video_true_start = time.monotonic()
        self._bind(page)

    def _new_context(self) -> Any:
        """Open a BrowserContext, recording video into `record_video_dir` when one is configured.

        Every context carries `reduced_motion="reduce"` — the determinism lever (BE-0191 unit 5) that
        collapses the app-under-test's CSS transitions to instant — and the resolved device
        descriptor (BE-0228), so a fresh context (the crawl's `reset_context` erase) keeps emulating
        the same phone rather than falling back to desktop. Both match the starter's context.
        """
        kwargs: dict[str, Any] = {"reduced_motion": "reduce", **self._resolved_device_kwargs()}
        if self._record_video_dir:
            kwargs["record_video_dir"] = str(self._record_video_dir)
        return self._browser.new_context(**kwargs)

    def _resolved_device_kwargs(self) -> dict[str, Any]:
        """The device descriptor for the current mode (BE-0228), resolved once against the live pw.

        A preset's descriptor is fixed data, so it is cached: the desktop default resolves to an
        empty mapping without ever touching `playwright.devices`.
        """
        if self._device_kwargs is None:
            self._device_kwargs = _device_context_kwargs(self._pw, self._device_mode)
        return self._device_kwargs

    def _bind(self, page: _Page) -> None:
        """Adopt a freshly created page (a new context, or a relaunched browser) as the live page.

        Clear the consuming health buffers and (re)register the dialog / health handlers on it, so
        the new page starts with a clean signal slate.
        """
        self._page = page
        self._page_errors = []
        self._last_nav_status = None
        self._dialogs = []
        self._cdp = None  # the old CDP session belonged to the previous context; re-open lazily
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

    # --- interval evidence (web equivalents of the simctl video / deviceLog providers) ---

    def driver_interval(self, kind: str, path: Path) -> intervals.Interval | None:
        """A whole-scenario interval recording for the web backend, or None if unsupported.

        The device pool hands this to the `FileSink` (the driver-supplied interval seam, shared with
        the adb backend) so the same `capture` policy that drives the simctl providers on iOS drives
        Playwright-native ones on web. `deviceLog` streams the
        browser console + uncaught page errors (the os_log analogue); `video` finalizes and
        collects the BrowserContext recording (only when a record dir was configured for this lane).
        """
        if kind == "deviceLog":
            return self._console_interval(path)
        if kind == "video":
            return self._video_interval(path)
        return None

    def _video_interval(self, path: Path) -> intervals.Interval | None:
        """Finalize the context's video recording into `path` on stop, if recording is enabled."""
        if self._record_video_dir is None:
            return None  # this lane was not asked to record (video not in the capture policy)
        driver = self

        class _VideoCapture:
            def stop(self, sig: int, timeout: float) -> None:
                # timeout is unused: Playwright finalizes the context's video synchronously here,
                # with no child process to wait on; the signature matches the intervals.Proc protocol.
                driver._finalize_video(path)

        return intervals.Interval(
            kind="video",
            path=path,
            provider=self.name,
            true_start=self._video_true_start,
            _proc=_VideoCapture(),
        )

    def _finalize_video(self, target: Path) -> None:
        """Close the context (Playwright writes the file on close), then move the video to `target`.

        Called when the scenario's steps are done, so closing the context early is safe; the later
        `close()` tears down the browser regardless.
        """
        video = getattr(self._page, "video", None)
        if self._context is not None:
            with contextlib.suppress(*_playwright_error_types()):
                self._context.close()
            self._context = None  # finalized; the lease's close() just stops the browser
            self._cdp = None  # its CDP session went with the closed context
            # Its true_start went with it too: a later recording context's Interval must never be
            # built with a stale instant belonging to this one.
            self._video_true_start = None
        if video is None:
            return
        # Let a failed move surface (like the iOS interval providers): swallowing it would record a
        # video artifact path that doesn't exist, turning a real problem into a silent one.
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(video.path(), str(target))

    def _console_interval(self, path: Path) -> intervals.Interval:
        """Stream the live page's console messages and uncaught errors to `path` until stopped."""
        sink = path.open("w", encoding="utf-8")
        page = self._page

        def on_console(msg: Any) -> None:
            with contextlib.suppress(Exception):
                sink.write(f"[{msg.type}] {msg.text}\n")

        def on_pageerror(error: Any) -> None:
            with contextlib.suppress(Exception):
                sink.write(f"[pageerror] {error}\n")

        on = getattr(page, "on", None)
        if on is not None:
            on("console", on_console)
            on("pageerror", on_pageerror)

        class _ConsoleCapture:
            def stop(self, sig: int, timeout: float) -> None:
                # timeout is unused: detaching listeners is instant (no child process to wait on),
                # but the signature matches the intervals.Proc protocol.
                remove = getattr(page, "remove_listener", None)
                if remove is not None:
                    # Suppress per call so a failure detaching one listener still detaches the other.
                    with contextlib.suppress(Exception):
                        remove("console", on_console)
                    with contextlib.suppress(Exception):
                        remove("pageerror", on_pageerror)
                sink.close()

        return intervals.Interval(
            kind="deviceLog", path=path, provider=self.name, _proc=_ConsoleCapture()
        )

    # --- lifecycle (web equivalents of simctl.Env launch/erase/terminate) ---

    @_wedge_guard
    def navigate(self) -> None:
        """Go to the configured base URL — the `launch` equivalent."""
        self._page.goto(self._base_url)

    @_wedge_guard
    def reset_context(self) -> None:
        """The crawl's clean start (the `erase` equivalent).

        Open a fresh BrowserContext + page — no cookies / storage / history carried across frontier
        visits — then navigate to the base URL. Cheap (no browser-process restart), and it lets a
        `path_to` recorded in one worker's browser replay from the same clean state in another
        (BE-0077). An injected test page has no contexts, so it just re-navigates.
        """
        if self._browser is not None:
            # Discard the current context (its cookies / storage / history) and open a clean one, so
            # at most one context is alive per worker — the per-visit `erase`, not a slow leak.
            if self._context is not None:
                with contextlib.suppress(*_playwright_error_types()):
                    self._context.close()
            self._context = self._new_context()
            self._bind(self._context.new_page())
            if self._record_video_dir:
                self._video_true_start = time.monotonic()
        self.navigate()

    def relaunch(self) -> None:
        """Tear down a wedged browser process and start a fresh one (BE-0077 fault isolation).

        Unlike `reset_context` (a cheap fresh context inside the same browser), this discards the
        whole browser — the unit the crawl hard-kills when a renderer crashes, a page hangs, or a
        navigation times out; the worker's next reset re-navigates the fresh process. A no-op for
        an injected test page (no real browser to relaunch).
        """
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
        # The browser may already be dead (target closed) by the time the lease ends, the same
        # "expected, not a defect" case `relaunch` above already suppresses per handle — but the
        # `pw` process must still be stopped or it leaks, so each handle gets its own guard rather
        # than one combined try (BE-0342: the pool's end-of-lease teardown now escalates anything
        # this doesn't suppress into a run-failing wiring defect). Clearing the handles afterwards,
        # like `relaunch` does, makes a second call (the caller's own failure path, per
        # `launch_driver`'s contract) a genuine no-op instead of re-entering an already-stopped `pw`
        # — whose failure is a driver-connection error, not a `playwright.sync_api.Error`, so the
        # suppress above wouldn't cover it.
        pw_errors = _playwright_error_types()
        if self._browser is not None:
            with contextlib.suppress(*pw_errors):
                self._browser.close()
        if self._pw is not None:
            with contextlib.suppress(*pw_errors):
                self._pw.stop()
        self._pw = self._browser = self._context = None

    # --- Driver Protocol ---

    @_wedge_guard
    def query(self) -> list[base.Element]:
        records = self._page.evaluate(QUERY_JS)
        return parse_dom(records if isinstance(records, list) else [])

    def _center(self, sel: base.Selector) -> base.Point:
        point, _ = self._center_with_element(sel)
        return point

    def _center_with_element(self, sel: base.Selector) -> tuple[base.Point, base.Element]:
        """The resolved element's frame center, plus the element (for the actuation record)."""
        el = base.resolve_unique(self.query(), sel)
        return base.frame_center(el["frame"]), el

    def _point_hits(self, point: base.Point, el: base.Element) -> _HitResult:
        """Whether `document.elementFromPoint` actually resolves to `el`, not an unrelated cover.

        Generalizes the `elementFromPoint` pattern already used by `select_option` below: walk the
        hit's ancestor chain looking for `el` — by its `data-testid` (the same attribute `QUERY_JS`,
        `bajutsu/dom.py`, reads into `identifier`) when it has one, or, when it does not (an element
        `QUERY_JS` matched by tag/role/`aria-label` instead), by matching bounding rects *and* the
        same accessible name `el["label"]` carries (`bajutsu/dom.py`'s own `aria-label` /
        `textContent` precedence, truncated to the same 200 chars). The name check guards against the
        geometry-only ambiguity a rect match alone would have: a transparent click-blocking overlay
        sized to exactly cover a button shares its rect but, unlike the button itself, essentially
        never shares its name. When `el["label"]` is `None` (no accessible name at all), the rect
        match alone is all that is available, the same residual ambiguity `topmost_at_point` accepts
        for an identically-framed pair on the other backends. A hit chain that never reaches `el`
        means an unrelated element genuinely covers the point — named in the returned
        `_HitResult.cover` / `.rect` (by `data-testid`, else tag plus DOM `id`) the same way
        `base.raise_if_covered` names a cover on the other backends, so a failure message does not
        force reproducing the screen by hand to learn what blocked the tap.

        A point outside the current viewport is a different question this check does not answer:
        `elementFromPoint` returns `null` there regardless of occlusion (`query()`'s frames are
        viewport-relative, BE-0326, so a below-the-fold `el` is resolvable with a point past
        `window.innerHeight`), and treating that `null` as "covered" would make `tap` implicitly
        scroll a below-the-fold target into view — exactly the behavior `docs/drivers.md` documents
        as adb-only. So an off-viewport point is reported as hit here, leaving that case to the
        explicit `scroll` action rather than this occlusion check.
        """
        x, y = point
        tx, ty, tw, th = el["frame"]
        identifier = json.dumps(el["identifier"])
        label = json.dumps(el["label"])
        result = self._page.evaluate(
            "(() => {"
            "const describe = (node) => {"
            "  const r = node.getBoundingClientRect();"
            "  const testid = node.getAttribute('data-testid');"
            "  const cover = testid || (node.tagName.toLowerCase() + (node.id ? '#' + node.id : ''));"
            "  return {ok: false, cover: cover, rect: [r.x, r.y, r.width, r.height]};"
            "};"
            f"if ({x} < 0 || {y} < 0 || {x} >= window.innerWidth || {y} >= window.innerHeight) "
            "return {ok: true, cover: null, rect: null};"
            f"const hit = document.elementFromPoint({x}, {y});"
            "if (!hit) return {ok: false, cover: null, rect: null};"
            f"const identifier = {identifier};"
            "if (identifier !== null) {"
            '  return hit.closest(`[data-testid="${CSS.escape(identifier)}"]`) !== null'
            "    ? {ok: true, cover: null, rect: null} : describe(hit);"
            "}"
            f"const label = {label};"
            "let node = hit;"
            "while (node) {"
            "  const r = node.getBoundingClientRect();"
            f"  const sameRect = Math.abs(r.x - {tx}) < 1 && Math.abs(r.y - {ty}) < 1"
            f"      && Math.abs(r.width - {tw}) < 1 && Math.abs(r.height - {th}) < 1;"
            "  const name = (node.getAttribute('aria-label') || node.textContent.trim()).slice(0, 200);"
            "  const sameName = label === null || name === label;"
            "  if (sameRect && sameName) {"
            "    return {ok: true, cover: null, rect: null};"
            "  }"
            "  node = node.parentElement;"
            "}"
            "return describe(hit);"
            "})()"
        )
        rect = result["rect"]
        return _HitResult(
            ok=result["ok"],
            cover=result["cover"],
            rect=(rect[0], rect[1], rect[2], rect[3]) if rect is not None else None,
        )

    @_wedge_guard
    def is_tappable(self, sel: base.Selector) -> bool:
        """Whether `sel` resolves to a unique element that a click at its center would actually hit.

        A pure query: no actuation, so the scroll-recovery loop can call it repeatedly with no side
        effects. Not found means "not tappable" (`False`), matching every other backend's convention
        for a target not yet in the tree; an ambiguous selector still raises `AmbiguousSelector`
        immediately, since occlusion is a different question from selector ambiguity.
        """
        try:
            point, el = self._center_with_element(sel)
        except base.ElementNotFound:
            return False
        return self._point_hits(point, el).ok

    def _center_checked(self, sel: base.Selector) -> tuple[base.Point, base.Element]:
        """`_center_with_element`, but raises `ElementNotTappable` when the point does not hit `sel`.

        The one seam `tap` / `double_tap` / `long_press` route through, so the check applies once
        rather than being duplicated at each call site.
        """
        point, el = self._center_with_element(sel)
        hit = self._point_hits(point, el)
        if not hit.ok:
            named = f" ({hit.cover!r} at {hit.rect})" if hit.cover is not None else ""
            raise base.ElementNotTappable(
                f"element resolved but covered by another element{named}: {sel!r}"
            )
        return point, el

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

    def _log_coordinate(
        self,
        gesture: str,
        point: base.Point,
        el: base.Element,
        *,
        duration_s: float | None = None,
        scale: float | None = None,
        radians: float | None = None,
    ) -> None:
        """Record a click / gesture the driver aimed at a point it computed itself."""
        self._actuations.record(
            Actuation(
                gesture=gesture,
                via="coordinate",
                unit=_UNIT,
                points=(point,),
                frame=el["frame"],
                target=el["identifier"],
                duration_s=duration_s,
                scale=scale,
                radians=radians,
            )
        )

    @_wedge_guard
    def tap(self, sel: base.Selector) -> None:
        (x, y), el = self._center_checked(sel)
        self._log_coordinate("tap", (x, y), el)
        self._page.mouse.click(x, y)

    @_wedge_guard
    def tap_point(self, p: base.Point) -> None:
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        self._page.mouse.click(p[0], p[1])

    @_wedge_guard
    def double_tap(self, sel: base.Selector) -> None:
        (x, y), el = self._center_checked(sel)
        self._log_coordinate("doubleTap", (x, y), el)
        self._page.mouse.dblclick(x, y)

    @_wedge_guard
    def long_press(self, sel: base.Selector, duration: float) -> None:
        (x, y), el = self._center_checked(sel)
        self._log_coordinate("longPress", (x, y), el, duration_s=duration)
        self._page.mouse.move(x, y)
        self._page.mouse.down()
        time.sleep(duration)
        self._page.mouse.up()

    @_wedge_guard
    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="swipe", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        # A literal pointer drag — the coordinate `swipe` form (canvas / map pan) and the `drag`
        # action (a resize divider, a slider thumb). Keyed on input mode like `scroll` (BE-0227): a
        # touch context uses a real touch drag (the pinch/rotate path) so a touch-bound handle
        # responds — a synthesized mouse drag fires no touch listeners; a desktop context uses a raw
        # mouse drag. (The directional "scroll" form does not come here — it goes to `scroll`.)
        if self._has_touch():
            self._touch_drag([frm], [to])
        else:
            self._page.mouse.move(frm[0], frm[1])
            self._page.mouse.down()
            self._page.mouse.move(to[0], to[1])
            self._page.mouse.up()

    @_wedge_guard
    def scroll(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="scroll", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        # A directional scroll (see base.Driver.scroll). A mouse drag leaves the page inert, so
        # dispatch the primitive that actually scrolls, keyed on the context's input mode (BE-0228): a
        # touch context uses a real single-finger touch drag (the pinch/rotate path, so touch/scroll
        # listeners fire); a desktop context wheels at the gesture's start, the delta being the
        # reverse of the travel (frm - to) so the page scrolls the way the gesture points.
        if self._has_touch():
            self._touch_drag([frm], [to])
        else:
            self._page.mouse.move(frm[0], frm[1])
            self._page.mouse.wheel(frm[0] - to[0], frm[1] - to[1])
        # `wheel` / touch dispatch returns before the compositor applies the scroll, so a `query()`
        # fired immediately after (the `scroll` action re-queries every step) would read the pre-scroll
        # layout and mistake a real scroll for end-of-content. Honor the non-inertial "advance and
        # stop" contract (BE-0326): wait on the paint loop until the scroll position holds steady for
        # two consecutive frames — a condition wait, not a fixed sleep. An instantaneous wheel settles
        # in a few frames; if the page animates the scroll (a JS handler calling smooth `scrollTo`),
        # this waits it out rather than capturing a mid-flight frame a following `tap` would miss. The
        # frame cap keeps a pathological never-settling page from hanging the step.
        self._page.evaluate(
            "() => new Promise(resolve => {"
            "  let last = null, steady = 0, frames = 0;"
            "  const tick = () => {"
            "    const p = window.scrollX + ',' + window.scrollY;"
            "    steady = p === last ? steady + 1 : 0;"
            "    last = p;"
            "    if (steady >= 2 || ++frames >= 30) resolve();"
            "    else requestAnimationFrame(tick);"
            "  };"
            "  requestAnimationFrame(tick);"
            "})"
        )

    @_wedge_guard
    def viewport(self) -> base.Point:
        # The true viewport for the `scroll` stop condition (BE-0326). `query()` reads
        # `getBoundingClientRect`, so frames are viewport-relative and off-screen DOM nodes stay in
        # the tree — `screen_size_from_elements` would overshoot the viewport by the content extent.
        # `window.innerWidth`/`innerHeight` report the real viewport in that same coordinate space.
        size = self._page.evaluate("() => [window.innerWidth, window.innerHeight]")
        return (float(size[0]), float(size[1]))

    def _has_touch(self) -> bool:
        """Whether the active context takes touch input, deciding a gesture's primitive (BE-0227).

        Read from the memoized device descriptor (BE-0228), which is derived from the target's fixed
        `deviceMode` and re-applied identically at every context creation — so it never goes stale
        across a `reset_context` or `relaunch`, unlike a value read back from a live context.
        """
        return bool(self._resolved_device_kwargs().get("has_touch", False))

    @_wedge_guard
    def back(self) -> None:
        # The web's "back" is browser history; the platform peer of Android's system back key and
        # iOS's OS back button (BE-0210).
        self._actuations.record(Actuation(gesture="back", via="history", unit=_UNIT))
        self._page.go_back()

    @_wedge_guard
    def pinch(self, sel: base.Selector, scale: float) -> None:
        # Two fingers level on the element's center; `scale` spreads (>1) or closes (<1) their gap.
        (cx, cy, r), el = self._gesture_anchor(sel)
        # The anchor: `gesture_anchor`'s rule plus the frame and the scale determine both contacts.
        self._log_coordinate("pinch", (cx, cy), el, scale=scale)
        start = [(cx - r, cy), (cx + r, cy)]
        end = [(cx - r * scale, cy), (cx + r * scale, cy)]
        self._touch_drag(start, end)

    @_wedge_guard
    def rotate(self, sel: base.Selector, radians: float) -> None:
        # Two fingers level on the center, rotated about it by `radians`.
        (cx, cy, r), el = self._gesture_anchor(sel)
        self._log_coordinate("rotate", (cx, cy), el, radians=radians)
        start = [(cx - r, cy), (cx + r, cy)]
        end = [_rotate_point(p, (cx, cy), radians) for p in start]
        self._touch_drag(start, end)

    def _gesture_anchor(
        self, sel: base.Selector
    ) -> tuple[tuple[float, float, float], base.Element]:
        """The element's center and a finger half-distance for a two-finger gesture (BE-0251).

        The resolved element travels with them so the caller records what it actuated.
        """
        el = base.resolve_unique(self.query(), sel)
        return base.gesture_anchor(el["frame"]), el

    def _touch_drag(self, start: list[base.Point], end: list[base.Point], steps: int = 5) -> None:
        """Synthesize a touch drag from `start` to `end` via CDP touch events (Chromium).

        One point per finger: two for `pinch` / `rotate`, one for a `scroll` on a touch context
        (BE-0227). Playwright's `mouse` is single-pointer, so touch goes through the DevTools
        protocol's `Input.dispatchTouchEvent` — the same path a real gesture takes, so the page's
        touch / gesture listeners fire, unlike a synthetic DOM event.
        """
        self._dispatch_touch("touchStart", start)
        for k in range(1, steps + 1):
            t = k / steps
            self._dispatch_touch(
                "touchMove",
                [
                    (s[0] + (e[0] - s[0]) * t, s[1] + (e[1] - s[1]) * t)
                    for s, e in zip(start, end, strict=True)
                ],
            )
        self._dispatch_touch("touchEnd", [])

    def _dispatch_touch(self, event_type: str, points: list[base.Point]) -> None:
        self._cdp_session().send(
            "Input.dispatchTouchEvent",
            {
                "type": event_type,
                "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(points)],
            },
        )

    def _cdp_session(self) -> Any:
        """The page's Chromium DevTools session, created once and reused for touch synthesis."""
        if self._cdp is None:
            self._cdp = cast(Any, self._page).context.new_cdp_session(self._page)
        return self._cdp

    @_wedge_guard
    def type_text(self, text: str) -> None:
        # The orchestrator taps `into` before this (see _do_type), focusing the field — same
        # contract every backend relies on, so typing always lands in the just-focused element.
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        self._page.keyboard.type(text)

    @_wedge_guard
    def delete_text(self, count: int) -> None:
        # `count` backspaces on the focused field (BE-0265). `press` per key, since Playwright has no
        # repeat-count on a single press.
        self._actuations.record(Actuation(gesture="deleteText", via="focused", unit=_UNIT))
        for _ in range(count):
            self._page.keyboard.press("Backspace")

    @_wedge_guard
    def select_all(self) -> None:
        # Ctrl+A selects the focused field's whole content (BE-0265).
        self._actuations.record(Actuation(gesture="selectAll", via="focused", unit=_UNIT))
        self._page.keyboard.press("Control+a")

    @_wedge_guard
    def copy_selection(self) -> None:
        # Ctrl+C copies the active selection to the clipboard (BE-0265).
        self._actuations.record(Actuation(gesture="copy", via="focused", unit=_UNIT))
        self._page.keyboard.press("Control+c")

    @_wedge_guard
    def select_option(self, sel: base.Selector, option: str) -> None:
        # A native <select>'s dropdown isn't in the DOM, so a coordinate click can't switch it
        # deterministically. Resolve the <select> through the determinism core (unique match), then
        # locate it at the resolved point — the same coordinate a click would use, keeping matching
        # in resolve_unique rather than Playwright's engine — and set its value, firing `change` so
        # the page's listeners run exactly as for a user selection (BE-0191).
        #
        # JS returns a sentinel string instead of throwing: a JS Error from evaluate() passes
        # through _wedge_guard as a generic simctl.DeviceError (indistinguishable from a browser
        # crash), so the two failure modes — not a <select>, option value absent — are surfaced as
        # sentinel strings and re-raised here as ElementNotFound (a SelectorError) so the run loop
        # can catch them with the same handler as any other selector failure.
        #
        # Resolves through `_center_checked`, not `_center_with_element`: an overlay covering the
        # <select> makes `elementFromPoint` return the overlay below, `closest('select')` come back
        # null, and the old unchecked path raise the factually wrong "not a <select>" — the selector
        # did resolve to a <select>, occlusion is why the point misses it. Checking first raises
        # `ElementNotTappable` naming the actual cover, like every other web tap-family failure.
        (x, y), el = self._center_checked(sel)
        # `option` never reaches the record: like a typed string, it can hold a resolved secret.
        self._log_coordinate("selectOption", (x, y), el)
        opt = json.dumps(option)
        result = self._page.evaluate(
            "(() => {"
            f"const el = document.elementFromPoint({x}, {y});"
            "const select = el && el.closest('select');"
            "if (!select) return 'no-select';"
            f"if (![...select.options].some(o => o.value === {opt})) return 'no-option';"
            f"select.value = {opt};"
            "select.dispatchEvent(new Event('change', {bubbles: true}));"
            "return 'ok';"
            "})()"
        )
        if result == "no-select":
            raise base.ElementNotFound(f"selectOption: resolved element is not a <select>: {sel!r}")
        if result == "no-option":
            raise base.ElementNotFound(f"selectOption: no option with value {option!r}: {sel!r}")

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # BE-0316 is an iOS SpringBoard concept: the web backend has no OS-level permission prompt at
        # all, so it never advertises the capability and preflight rejects the step. Mid-run backstop.
        raise base.UnsupportedAction(
            "handleSystemAlert is iOS-only; the web backend has no OS-level permission prompt"
        )

    def system_alert_labels(self) -> list[str]:
        # No OS-level SpringBoard prompt on the web; the reactive guard's native path never runs here.
        return []

    @_wedge_guard
    def wait_for(self, sel: base.Selector) -> bool:
        # Single-shot by contract (BE-0118): delegates to the shared base.default_wait_for so the
        # four backends share one body; the deadline poll lives in base.wait_until, so the timeout
        # is honoured on Web exactly as on the other backends (BE-0251).
        return base.default_wait_for(self, sel)

    @_wedge_guard
    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path)

    def network_collector(self, mocks: list[Mock] | None = None) -> WebNetworkCollector:
        """A collector hooked to this driver's page (Playwright sees every request natively).

        Lets the run loop's `request` assertion + network evidence work on web (BE-0054). `mocks` are
        fulfilled in-process via `page.route`. Imported lazily to keep the page private.
        """
        from bajutsu.web_network import WebNetworkCollector

        return WebNetworkCollector(self._page, mocks)

    # Playwright has a genuine semantic click, native auto-waiting, native network observation +
    # stubbing, and (via CDP touch synthesis) two-finger gestures (BE-0054). Class constant so the
    # preflight (BE-0082) reads it via `backends.capabilities_for` without starting a browser.
    CAPABILITIES = frozenset(
        {
            base.Capability.QUERY,
            base.Capability.ELEMENTS,
            base.Capability.SCREENSHOT,
            base.Capability.SEMANTIC_TAP,
            base.Capability.CONDITION_WAIT,
            base.Capability.NETWORK,
            base.Capability.MULTI_TOUCH,
            base.Capability.SELECT_OPTION,
            base.Capability.TEXT_SELECTION,
        }
    )

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)
