"""Tests for the HTML report screenshot/element-viewer, tree, and video."""

from __future__ import annotations

import json
from pathlib import Path

from _report import _el, _passing

from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult, StepOutcome
from bajutsu.report import html_report


def test_screenshot_or_tree_button_opens_element_viewer_and_arrows_navigate() -> None:
    # Clicking a step's screenshot — or the "tree" button — opens the element viewer; ← / → walk
    # the steps of the current scenario, looping. Inside the viewer, clicking the screenshot
    # enlarges it full-screen (a #imgz lightbox), where ← / → also walk the steps' screenshots.
    out = html_report("run1", [_passing()])
    assert "closest('.treebtn') || e.target.closest('.shot')" in out  # both open the element viewer
    assert (
        'id="imgz"' in out and "openImg(imEl.getAttribute('src'))" in out
    )  # viewer screenshot → enlarge
    assert "imgzSync" in out  # the enlarged view's ← / → drive the viewer and mirror its screenshot
    assert "ArrowLeft" in out and "ArrowRight" in out  # arrow keys walk the steps
    # navigation is scoped to one scenario (details.scn) and wraps at the ends
    assert "tvScopeFor" in out and "details.scn" in out and "% tvScope.length" in out


def test_step_click_seeks_without_autoplay() -> None:
    # Clicking a step seeks the recording but never starts playback on a paused video.
    # Playback is started only from the explicit play/pause control, never the seek path.
    out = html_report("run9", [_passing()])
    assert "v.currentTime = t;" in out  # step-row click seeks
    assert "if(v.paused) v.play();" in out  # play() is reachable only via the button
    # The seek handler stays seek-only (it has no .play() of its own).
    assert "Seek only" in out


def test_html_embeds_scenario_video() -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    out = html_report("run9", [r])
    assert "<video" in out
    assert 'src="00-s1/scenario.mp4"' in out
    # A scenario with no video artifact embeds no player.
    assert "<video" not in html_report("run9", [_passing()])


def test_html_step_rows_carry_video_offset() -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, duration_s=0.2, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, duration_s=1.1, started_at=1.5),
        ],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    out = html_report("run1", [r])
    # Each step row is clickable and tagged with its offset into the recording…
    assert "class='srow ok' data-t='0.000'" in out
    assert "data-t='1.500'" in out
    # …and the JS seeks the video and highlights the playing step.
    assert "v.currentTime = t" in out
    assert "timeupdate" in out and "playing" in out


def test_html_derives_the_video_offset_from_absolute_timestamps() -> None:
    # A step records the absolute instant it began; the seek offset is derived here, at render time,
    # by subtracting the scenario's video anchor (BE-0348) — so an improved anchor makes an already
    # recorded run seek correctly without re-running it. The anchor precedes the first step by 2.5s
    # (a prestarted recording), which is what pushes that step off 0.0.
    anchor = 1_700_000_000.0
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, duration_s=0.2, started_at=anchor + 2.5),
            StepOutcome(index=1, action="wait", ok=True, duration_s=1.1, started_at=anchor + 4.0),
        ],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
        video_anchor_s=anchor,
    )
    out = html_report("run1", [r])
    assert "class='srow ok' data-t='2.500'" in out
    assert "data-t='4.000'" in out
    # The raw epoch must never reach the page — that would seek the player past the end of any clip.
    assert str(int(anchor)) not in out


def test_html_step_offsets_survive_a_run_recorded_before_the_anchor_was_persisted() -> None:
    # A pre-BE-0348 manifest carries already-relative `started_at` values and no `video_anchor_s`,
    # so the anchor reconstructs at 0.0 and the derivation returns them unchanged (BE-0068's spirit:
    # an older run still renders rather than failing).
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, started_at=1.5)],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    assert "data-t='1.500'" in html_report("run1", [r])


def test_html_shows_step_screenshot_and_tree(tmp_path: Path) -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    step_dir = tmp_path / "00-s1" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(
        json.dumps([_el("home.title", "Welcome", ["staticText"])]), encoding="utf-8"
    )
    out = html_report("run1", [r], tmp_path)
    # the step's screenshot thumbnail and its element viewer are shown
    assert 'class="shot"' in out and 'src="00-s1/step0/after.png"' in out
    # the element tree opens in-report (no new tab): a button + inline embedded data,
    # rendered into the #tv overlay rather than linking out to the json file.
    assert 'class="elnk treebtn"' in out
    assert 'target="_blank"' not in out
    assert "home.title" in out and "Welcome" in out
    assert 'id="tv"' in out and "tvFilter" in out
    # the screenshot and the tree button both open the element viewer (not the old "lb" preview); it
    # shows the step's own info above the table.
    assert 'id="lb"' not in out and "openLightbox" not in out
    assert 'class="tv-step"' in out
    # the ◀ N/M ▶ step controls are built below the element list (in JS), and the element filter
    # sits in its own band below the step info (not in the head).
    assert "tv-treenav" in out and "tv-prev" in out and "tv-next" in out
    assert "tv-pos" in out and "(tvIndex + 1) + '/' + tvScope.length" in out  # the N/M counter
    assert 'class="tv-filter"' in out


def test_html_tree_rows_carry_frame_for_screenshot_highlight(tmp_path: Path) -> None:
    # Each element row embeds its raw frame (points) and the table the screen rect, so
    # the viewer can highlight the hovered element's location on the screenshot.
    el = {**_el("home.cta", "Buy", ["button"]), "frame": (12.0, 40.0, 100.0, 36.0)}
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    step_dir = tmp_path / "00-s1" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps([el]), encoding="utf-8")
    out = html_report("run1", [r], tmp_path)
    # the row carries the frame; the table carries the screen extent (bbox: 112x76)
    assert 'class="tvrow" data-x="12" data-y="40" data-w="100" data-h="36"' in out
    assert 'data-sw="112" data-sh="76"' in out
    # the highlight overlay + frame wrapper are wired in JS/CSS
    assert "tv-hl" in out and "tv-shotframe" in out


def _one_step_report(tmp_path: Path, artifacts: list[Artifact]) -> str:
    """Render a one-step report whose step recorded *artifacts* and one framed element."""
    el = {**_el("home.cta", "Buy", ["button"]), "frame": (12.0, 40.0, 100.0, 36.0)}
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0, artifacts=artifacts),
        ],
        expect_results=[],
        artifacts=[],
    )
    step_dir = tmp_path / "00-s1" / "step0"
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "elements.json").write_text(json.dumps([el]), encoding="utf-8")
    return html_report("run1", [r], tmp_path)


def test_step_shows_the_post_action_screenshot(tmp_path: Path) -> None:
    # A step recording both screenshots shows `after.png` — in the steps-table thumbnail and, since
    # the element viewer reads that same `img.shot`, in the viewer beside the element table.
    # `elements.json` has one fixed name, so the post-step `elements` capture the default
    # `defaults.capture` asks for leaves the embedded tree describing the post-action screen;
    # `before.png` would put a hovered element's highlight on pixels from another moment.
    out = _one_step_report(
        tmp_path,
        [
            Artifact("00-s1/step0/before.png", "screenshot", "driver"),
            Artifact("00-s1/step0/elements.json", "elements", "driver"),
            Artifact("00-s1/step0/after.png", "screenshot", "driver"),
            Artifact("00-s1/step0/elements.json", "elements", "driver"),
        ],
    )
    assert 'class="shot" loading="lazy" src="00-s1/step0/after.png"' in out
    assert "before.png" not in out


def test_step_falls_back_to_the_pre_action_screenshot_when_no_after_png_exists(
    tmp_path: Path,
) -> None:
    # A capture policy that never asks for `screenshot.after` records no `after.png`, so the step
    # keeps showing the pre-step baseline's `before.png` rather than nothing.
    out = _one_step_report(
        tmp_path,
        [
            Artifact("00-s1/step0/before.png", "screenshot", "driver"),
            Artifact("00-s1/step0/elements.json", "elements", "driver"),
        ],
    )
    assert 'class="shot" loading="lazy" src="00-s1/step0/before.png"' in out


def test_html_tree_falls_back_to_link_without_run_dir() -> None:
    # Structure-only render (no run_dir → no element data to embed): keep a link.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r])
    assert 'href="00-s1/step0/elements.json"' in out


def test_view_data_picks_after_png_over_the_earlier_screenshot() -> None:
    # `screenshot.before` and `screenshot.after` both produce kind="screenshot", so the step carries
    # two artifacts of one kind. Position does not decide between them: `after.png` wins wherever it
    # sits in the list, including — as here — after the `before.png` the pre-step baseline wrote.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/before.png", "screenshot", "driver"),
                    Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r])
    assert 'src="00-s1/step0/after.png"' in out
    assert 'src="00-s1/step0/before.png"' not in out
