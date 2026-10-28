"""`PlaywrightDriver.read_attribute` against a real headless Chromium (BE-0466).

The read rides the query's own walk and resolves through `resolve_unique`, so it can only be pinned
against a real DOM: whether `getAttribute` lands beside the right record, and whether a covered, an
off-viewport, or an iframe element reads the same as any other listed element. Like
`test_dom_query_js_web.py`, the `web` marker keeps it out of `make check` and `web-e2e.yml` runs it.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from typing import Any

import pytest

from bajutsu.common.drivers import base
from bajutsu.common.drivers.playwright import PlaywrightDriver

if importlib.util.find_spec("playwright") is None:
    pytest.skip("Playwright (the web extra) is not installed", allow_module_level=True)

_FRAME = '<a data-testid="framed" href="/inside">Inside</a>'

_PAGE = f"""
<a data-testid="docs.link" href="/docs" data-state="ready">Docs</a>
<a data-testid="bare">No href</a>
<button data-testid="busy" aria-busy="">Busy</button>
<div data-testid="covered" data-state="hidden" style="position:absolute;left:0;top:200px;width:80px;height:40px">C</div>
<div style="position:absolute;left:0;top:200px;width:200px;height:200px;background:#fff">cover</div>
<div data-testid="far" data-state="far" style="position:absolute;left:0;top:3000px;width:80px;height:40px">F</div>
<button data-testid="twin" data-n="1">Twin</button>
<button data-testid="twin" data-n="2">Twin 2</button>
<svg data-testid="icon" viewBox="0 0 10 10" width="10" height="10" role="img"><rect width="10" height="10"/></svg>
<iframe srcdoc='{_FRAME}' style="position:absolute;left:300px;top:0;width:200px;height:100px"></iframe>
"""


@pytest.fixture(scope="module")
def driver() -> Iterator[PlaywrightDriver]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page: Any = browser.new_page()
            page.set_content(_PAGE)
            page.wait_for_function(
                "() => document.querySelector('iframe').contentDocument.readyState === 'complete'"
            )
            yield PlaywrightDriver("about:blank", page=page)
        finally:
            browser.close()


@pytest.mark.web
def test_reads_through_an_identifier_and_a_label_selector(driver: PlaywrightDriver) -> None:
    assert driver.read_attribute({"id": "docs.link"}, "href") == "/docs"
    assert driver.read_attribute({"label": "Docs"}, "data-state") == "ready"


@pytest.mark.web
def test_missing_differs_from_empty(driver: PlaywrightDriver) -> None:
    assert driver.read_attribute({"id": "bare"}, "href") is None
    assert driver.read_attribute({"id": "busy"}, "aria-busy") == ""


@pytest.mark.web
def test_covered_and_off_viewport_elements_read_like_any_other(driver: PlaywrightDriver) -> None:
    assert driver.read_attribute({"id": "covered"}, "data-state") == "hidden"
    assert driver.read_attribute({"id": "far"}, "data-state") == "far"


@pytest.mark.web
def test_an_iframe_element_reads_from_its_own_document(driver: PlaywrightDriver) -> None:
    assert driver.read_attribute({"id": "framed"}, "href") == "/inside"


@pytest.mark.web
def test_the_name_reaches_getattribute_as_written(driver: PlaywrightDriver) -> None:
    # HTML attribute names match case-insensitively; an SVG element's match exactly, as the DOM does.
    assert driver.read_attribute({"id": "docs.link"}, "HREF") == "/docs"
    assert driver.read_attribute({"id": "icon"}, "viewBox") == "0 0 10 10"
    assert driver.read_attribute({"id": "icon"}, "viewbox") is None
    # Encoded, never spliced: a quote in the name cannot break out of the script.
    assert driver.read_attribute({"id": "docs.link"}, "x\"');throw 1;//") is None


@pytest.mark.web
def test_selector_failures_match_what_a_tap_raises(driver: PlaywrightDriver) -> None:
    with pytest.raises(base.ElementNotFound):
        driver.read_attribute({"id": "missing"}, "href")
    with pytest.raises(base.ElementNotFound):
        driver.tap({"id": "missing"})
    with pytest.raises(base.AmbiguousSelector):
        driver.read_attribute({"id": "twin"}, "data-n")
    with pytest.raises(base.AmbiguousSelector):
        driver.tap({"id": "twin"})
    assert driver.read_attribute({"id": "twin", "index": 1}, "data-n") == "2"


@pytest.mark.web
def test_the_query_snapshot_carries_no_attribute(driver: PlaywrightDriver) -> None:
    # Read on demand only: the element every query returns gains no field (BE-0466).
    for el in driver.query():
        assert "attribute" not in el
