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
from driver_conformance import ConformanceHarness, DriverConformanceContract

from bajutsu.drivers import base
from bajutsu.drivers.playwright import PlaywrightDriver

# Skip cleanly when the web extra is absent (running this file directly on a bare env). `find_spec`
# locates Playwright *without importing it*, so merely collecting this module never pulls the heavy
# dep into sys.modules — keeping `test_playwright.py`'s "importing the driver doesn't load
# playwright" invariant intact even when the extra is installed. The `@pytest.mark.web` below is
# what actually keeps this out of the fast gate; the real import is deferred into the fixture.
if importlib.util.find_spec("playwright") is None:
    pytest.skip("Playwright (the web extra) is not installed", allow_module_level=True)


def _render(elements: list[base.Element]) -> str:
    """One HTML page realizing the seeded conformance screen for `QUERY_JS` to read.

    Each element is a `data-testid` node with an explicit size and margin so it is visible,
    non-zero (`QUERY_JS` drops collapsed nodes), and on-screen (so the resolved center is a
    real, clickable point) — the seeded ids come through as the driver's element identifiers.
    An element seeded with the `button` trait renders as a `<button>` (which `QUERY_JS` maps back
    to that trait), so the cross-backend `{ label, traits: [button] }` case resolves on Chromium
    too (BE-0223); every other element stays a plain `<div>`.
    """
    nodes = "".join(_node(el) for el in elements)
    return f"<!doctype html><html><body>{nodes}</body></html>"


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
