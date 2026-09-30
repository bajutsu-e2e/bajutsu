"""Run the report's own script in a real Chromium to check the per-recording highlight lanes.

The string checks in `test_viewer.py` only prove the script ships; which rows land in which lane is
decided by `assignLanes` / `rowsFor` at page load, so only executing the page can check it. Needs
the `web` extra + a Chromium binary, so like `test_driver_conformance_web.py` it carries the `web`
marker (deselected by the fast gate) and skips outright when Playwright is absent.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from typing import Any

import pytest

from bajutsu.common.evidence import Artifact
from bajutsu.common.orchestrator import RunResult, StepOutcome, TargetDeviceInfo
from bajutsu.common.report import html_report

if importlib.util.find_spec("playwright") is None:
    pytest.skip("Playwright (the web extra) is not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def page() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            yield browser.new_page()
        finally:
            browser.close()


def _three_targets_two_videos() -> RunResult:
    # `api` declares steps but recorded no video, so its rows have no player of their own and fall
    # to the primary's `rowsFor` bucket. Its step runs first, so a label inferred from the primary's
    # first row would wrongly read `api`.
    return RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", target="api", ok=True, started_at=100.0),
            StepOutcome(index=1, action="tap", target="app", ok=True, started_at=101.0),
            StepOutcome(index=2, action="tap", target="web", ok=True, started_at=102.0),
        ],
        artifacts=[
            Artifact("00-s1/app/scenario.mp4", "video", "simctl"),
            Artifact("00-s1/web/scenario.webm", "video", "playwright", target="web"),
        ],
        video_anchor_s=100.0,
        target_video_anchors={"web": 101.0},
        target_devices={
            "app": TargetDeviceInfo(backend="xcuitest"),
            "web": TargetDeviceInfo(backend="playwright"),
            "api": TargetDeviceInfo(backend="http"),
        },
    )


@pytest.mark.web
def test_each_recording_and_its_own_rows_share_one_lane(page: Any) -> None:
    page.set_content(html_report("run1", [_three_targets_two_videos()]))
    players = page.eval_on_selector_all(
        ".player",
        "ps => ps.map(p => [p.className, p.querySelector('.tgtlbl')?.textContent || ''])",
    )
    assert players == [["player lane-0", "app"], ["player lane-1", "web"]]
    rows = page.eval_on_selector_all(
        "tr.srow[data-target]",
        "rs => rs.map(r => [r.dataset.target, [...r.classList].find(c => c.startsWith('lane-'))])",
    )
    # A target with no recording of its own shares the primary's lane, the player it seeks.
    assert rows == [["api", "lane-0"], ["app", "lane-0"], ["web", "lane-1"]]


@pytest.mark.web
def test_a_single_recording_gets_no_lane(page: Any) -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, started_at=100.0)],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
        video_anchor_s=100.0,
    )
    page.set_content(html_report("run1", [r]))
    assert page.eval_on_selector_all("[class*='lane-']", "es => es.length") == 0
    assert page.eval_on_selector_all(".player .tgtlbl", "es => es.length") == 0
