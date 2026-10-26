"""Evaluate `QUERY_JS` in a real headless Chromium to pin which DOM source each tag's `value` reads.

The fast suite can only string-match the script; whether `getAttribute('href')` and friends actually
land in the record needs a real DOM. Like `test_driver_conformance_web.py`, this needs the `web` extra
plus a Chromium binary: the `web` marker keeps it out of `make check`, `web-e2e.yml` runs it, and a
`find_spec` module-skip drops it when Playwright is absent.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from typing import Any

import pytest

from bajutsu.common.drivers.dom import QUERY_JS, parse_dom

if importlib.util.find_spec("playwright") is None:
    pytest.skip("Playwright (the web extra) is not installed", allow_module_level=True)

_PAGE = """
<a data-testid="link" href="/docs?q=1">Docs</a>
<a data-testid="anchor">No href</a>
<img data-testid="img" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="10" height="10">
<time data-testid="time" datetime="2026-10-26">Oct 26</time>
<input data-testid="field" value="typed">
<div data-testid="slider" role="slider" aria-valuenow="40" aria-valuetext="40 percent">s</div>
<div data-testid="meter" role="progressbar" aria-valuenow="7">p</div>
<button data-testid="btnslider" role="slider" aria-valuenow="5">b</button>
<input data-testid="range" type="range" value="30" aria-valuenow="99">
<input data-testid="rolerange" type="range" role="slider" value="30" aria-valuenow="99">
<progress data-testid="progress" role="progressbar" max="1" value="0.7" aria-valuenow="0"></progress>
<progress data-testid="busy" role="progressbar" aria-valuetext="Loading"></progress>
<div data-testid="emptytext" role="slider" aria-valuetext="" aria-valuenow="40">e</div>
"""


@pytest.fixture(scope="module")
def page() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(_PAGE)
            yield pg
        finally:
            browser.close()


@pytest.mark.web
def test_query_js_reads_each_tags_own_value_source(page: Any) -> None:
    values = {e["identifier"]: e["value"] for e in parse_dom(page.evaluate(QUERY_JS))}
    # The raw attribute, not the resolved `el.href`, so the value does not shift with the host/port.
    assert values["link"] == "/docs?q=1"
    assert values["anchor"] is None
    assert values["img"] == "data:image/gif;base64,R0lGODlhAQABAAAAACw="
    assert values["time"] == "2026-10-26"
    assert values["field"] == "typed"
    assert values["slider"] == "40 percent"
    assert values["meter"] == "7"
    # A role'd widget's ARIA value beats its host element's own empty `value` property...
    assert values["btnslider"] == "5"
    # ...but a native form control keeps its live `value`, role or not: aria-valuenow can lag it.
    assert values["range"] == "30"
    assert values["rolerange"] == "30"
    assert values["progress"] == "0.7"
    # An indeterminate <progress> has no value of its own, so its ARIA text is what it publishes.
    assert values["busy"] == "Loading"
    # An empty aria-valuetext (an unfilled template) must not hide a published aria-valuenow.
    assert values["emptytext"] == "40"
