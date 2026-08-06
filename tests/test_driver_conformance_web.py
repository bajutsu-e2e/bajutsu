"""Run the driver conformance contract (BE-0114) against the Playwright backend.

Unlike `test_driver_conformance.py` (FakeDriver, browser-free, on the fast Linux gate), this
drives a real headless Chromium: the point of the suite is to catch drift on a backend's own
query / action code, which only surfaces against the real browser, not the shared base alone.
So it needs the `web` extra + a Chromium binary and runs in the separate web CI job, never in
`make check`: a `web` pytest marker (deselected by the gate's default `-m 'not web'`) keeps it
out even when the extra is installed, and a `find_spec` module-skip drops it when Playwright is
absent — the fast gate's state, since the `web` extra is not in the dev group.

Each conformance screen is realized as real HTML: every seeded element becomes a visible,
non-zero-size node tagged with `data-testid` (the id convention `QUERY_JS` reads), rendered via
`page.set_content`, then driven through the real `PlaywrightDriver` on an injected page — so the
contract exercises the actual query → resolve → mouse-click / CDP-touch path against Chromium.
"""

from __future__ import annotations

import html
import importlib.util
from collections.abc import Iterator
from typing import Any

import pytest
from driver_conformance import (
    FIELD_ID,
    OBSTRUCTION_CLEAR_ID,
    OBSTRUCTION_COVER_ID,
    OBSTRUCTION_TARGET_ID,
    SCROLL_ROW_COUNT,
    SCROLL_ROW_PREFIX,
    SCROLL_TALL_ID,
    ConformanceHarness,
    DriverConformanceContract,
)

from bajutsu.drivers import base
from bajutsu.drivers.playwright import PlaywrightDriver

# Skip cleanly when the web extra is absent (running this file directly on a bare env). `find_spec`
# locates Playwright *without importing it*, so merely collecting this module never pulls the heavy
# dep into sys.modules — keeping `test_playwright.py`'s "importing the driver doesn't load
# playwright" invariant intact even when the extra is installed. The `@pytest.mark.web` below is
# what actually keeps this out of the fast gate; the real import is deferred into the fixture.
if importlib.util.find_spec("playwright") is None:
    pytest.skip("Playwright (the web extra) is not installed", allow_module_level=True)


# The always-present editable field (BE-0280), the web twin of the iOS `ConformanceView` /
# Compose `ConformanceScreen` field. Absolutely positioned at a known, off-flow box so its frame
# stays fixed (the coordinate-tap invariant aims at its center) and it never overlaps the seeded
# nodes above; a real `<input>` so `QUERY_JS` reads its live `value` for the round-trip invariant.
_FIELD_HTML = (
    f'<input data-testid="{FIELD_ID}" '
    'style="position:absolute;left:8px;top:400px;width:200px;height:40px">'
)


def _render(elements: list[base.Element]) -> str:
    """One HTML page realizing the seeded conformance screen for `QUERY_JS` to read.

    Each element is a `data-testid` node with an explicit size and margin so it is visible,
    non-zero (`QUERY_JS` drops collapsed nodes), and on-screen (so the resolved center is a
    real, clickable point) — the seeded ids come through as the driver's element identifiers.
    An element seeded with the `button` trait renders as a `<button>` (which `QUERY_JS` maps back
    to that trait), so the cross-backend `{ label, traits: [button] }` case resolves on Chromium
    too (BE-0223); every other element stays a plain `<div>`. The editable conformance field is
    always appended (BE-0280), like the app-side screens' field, so the text-editing / `tap_point`
    invariants have a live field regardless of what was seeded.
    """
    nodes = "".join(_node(el) for el in elements)
    return f"<!doctype html><html><body>{nodes}{_FIELD_HTML}</body></html>"


def _node(el: base.Element) -> str:
    tag = "button" if base.Trait.BUTTON in el["traits"] else "div"
    testid = html.escape(el["identifier"] or "", quote=True)
    label = html.escape(el["label"] or "")
    return (
        f'<{tag} data-testid="{testid}" style="width:100px;height:100px;margin:8px">{label}</{tag}>'
    )


class PlaywrightConformanceHarness:
    """Realizes a conformance screen as HTML in a headless Chromium the real driver drives.

    Holds one `PlaywrightDriver` over an injected live page; `with_screen` re-renders the page and
    returns that same driver, so the contract's interleaved act-then-reseed steps see the current
    screen without relaunching the browser per call.
    """

    backend = "playwright"

    def __init__(self, page: Any) -> None:
        self._page = page
        # Inject the real page: the driver's browser-launch path is skipped, but every action still
        # runs through its real query()/tap()/gesture code against Chromium — the drift the suite hunts.
        self._driver = PlaywrightDriver("about:blank", page=page)

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        self._page.set_content(_render(elements))
        return self._driver

    def scrollable_screen(self) -> base.Driver:
        # A document taller than the viewport (BE-0326): block rows stack down the page, so the lower
        # ones and the tall row start below the fold. They stay in the DOM (so `query()` reports them
        # with an out-of-viewport `getBoundingClientRect`), and the driver's real wheel scroll brings
        # the target's center into `window.innerHeight` — the exact viewport `viewport()` reports.
        rows = "".join(
            f'<div data-testid="{SCROLL_ROW_PREFIX}{i}" '
            f'style="height:90px;background:#ccc">row {i}</div>'
            for i in range(SCROLL_ROW_COUNT)
        )
        tall = (
            f'<div data-testid="{SCROLL_TALL_ID}" style="height:1400px;background:#aaa">tall</div>'
        )
        self._page.set_content(
            f"<!doctype html><html><body style='margin:0'>{rows}{tall}</body></html>"
        )
        return self._driver

    def obstruction_screen(self) -> base.Driver:
        # `cover` is later in the DOM than `target` and shares its top-left corner, so with no
        # z-index set, normal stacking order paints it on top — a real `document.elementFromPoint`
        # hit-test at `target`'s center resolves to `cover`, not `target` (BE-0326's own driver, not
        # a fake). `cover` is taller than `target` (30px vs. 20px) so the center point (y=10) lands
        # safely inside it rather than exactly on its bottom edge, which some engines read as just
        # outside the box. `clear` sits far below, alone, so nothing covers it.
        self._page.set_content(
            "<!doctype html><html><body style='margin:0'>"
            f'<div data-testid="{OBSTRUCTION_TARGET_ID}" '
            'style="position:absolute;left:0;top:0;width:100px;height:20px"></div>'
            f'<div data-testid="{OBSTRUCTION_COVER_ID}" '
            'style="position:absolute;left:0;top:0;width:300px;height:30px"></div>'
            f'<div data-testid="{OBSTRUCTION_CLEAR_ID}" '
            'style="position:absolute;left:0;top:500px;width:100px;height:20px"></div>'
            "</body></html>"
        )
        return self._driver


@pytest.fixture(scope="module")
def chromium() -> Iterator[Any]:
    # Imported here, not at module top, so collection stays free of the heavy dep (see the skip above).
    from playwright.sync_api import sync_playwright

    # The `with` form stops Playwright even if `launch()` raises — a bare `.start()` before the
    # try/finally would leak the process on a launch failure.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.mark.web
class TestPlaywrightDriverConformance(DriverConformanceContract):
    @pytest.fixture
    def harness(self, chromium: Any) -> Iterator[ConformanceHarness]:
        page = chromium.new_page()
        try:
            yield PlaywrightConformanceHarness(page)
        finally:
            page.close()


@pytest.mark.web
def test_is_tappable_true_for_a_below_the_fold_target_not_actually_covered(chromium: Any) -> None:
    """A resolved target past the viewport bottom reads as tappable, not "covered".

    `document.elementFromPoint` returns `null` for any point outside the viewport regardless of
    occlusion (`query()`'s frames are viewport-relative, so a below-the-fold element is still
    resolvable with a center past `window.innerHeight`). Reading that `null` as "covered" would
    make `tap` implicitly scroll a below-the-fold target into view through the occlusion check —
    behavior `docs/drivers.md` documents as adb-only. This target has nothing drawn over it at
    all; it is merely past the bottom of a deliberately short viewport.
    """
    page = chromium.new_page()
    try:
        page.set_viewport_size({"width": 800, "height": 200})
        driver = PlaywrightDriver("about:blank", page=page)
        page.set_content(
            '<div data-testid="below-fold" '
            'style="position:absolute;left:0;top:900px;width:100px;height:20px"></div>'
        )
        assert driver.is_tappable({"id": "below-fold"}) is True
    finally:
        page.close()


@pytest.mark.web
def test_native_checkbox_checked_reads_as_selected(chromium: Any) -> None:
    """A native checkbox's live checked state must surface as the `selected` trait.

    ARIA-free pages (a bare `<input type=checkbox>`, like the serve UI's theme switch) carry
    their state only on the DOM property, so `QUERY_JS` must read `el.checked` — the web
    equivalent of a UISwitch's value — for the DSL's `selected` assertion to see it.
    """
    page = chromium.new_page()
    try:
        driver = PlaywrightDriver("about:blank", page=page)
        page.set_content(
            '<input type="checkbox" data-testid="on" checked>'
            '<input type="checkbox" data-testid="off">'
        )
        by_id = {el["identifier"]: el for el in driver.query()}
        assert base.Trait.SELECTED in by_id["on"]["traits"]
        assert base.Trait.SELECTED not in by_id["off"]["traits"]
    finally:
        page.close()
