"""Tests for the HTML report's video expand overlay."""

from __future__ import annotations

from _report import _passing

from bajutsu.common.evidence import Artifact
from bajutsu.common.orchestrator import RunResult, StepOutcome
from bajutsu.common.report import html_report


def test_expand_button_rendered_beside_play_button() -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    out = html_report("run1", [r])
    assert 'aria-label="play / pause">▶</button><button class="vexpand"' in out
    # A scenario with no video artifact renders no player, and so no expand button either (the
    # class name itself still appears in the report's bundled CSS/JS, present on every page).
    assert 'class="vexpand" type="button" aria-label="expand video"' not in html_report(
        "run1", [_passing()]
    )


def test_expand_button_rendered_once_per_player() -> None:
    # BE-0428: a multi-target scenario's players (one per declared target's own recording) each
    # get their own expand button, not just the primary's.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", target="app", ok=True, started_at=100.0),
            StepOutcome(index=1, action="tap", target="web", ok=True, started_at=104.0),
        ],
        expect_results=[],
        artifacts=[
            Artifact("00-s1/scenario.mp4", "video", "simctl"),
            Artifact("00-s1/web/scenario.webm", "video", "playwright", target="web"),
        ],
        video_anchor_s=100.0,
        target_video_anchors={"web": 102.0},
    )
    out = html_report("run1", [r])
    assert out.count("<video ") == 2
    assert out.count('class="vexpand" type="button" aria-label="expand video"') == 2


def test_video_expand_modal_skeleton_present() -> None:
    out = html_report("run1", [_passing()])
    assert 'class="vz" id="vz"' in out
    assert 'class="vz-close" type="button" aria-label="close"' in out
    assert 'class="vz-tabs" hidden' in out
    assert 'class="vz-video"' in out
    assert 'class="vz-steps"' in out


def test_video_expand_js_wires_open_mount_restore_and_close() -> None:
    out = html_report("run1", [_passing()])
    assert "function vzOpen(player)" in out
    assert "function vzMount(player)" in out
    assert "function vzRestore()" in out
    assert "function vzBuildSteps(player)" in out
    assert "function vzClose()" in out
    assert "videoHome.set(v, p)" in out
    # The expand button's click is delegated the same way every other overlay trigger is.
    assert "closest('.vexpand')" in out


def _function_body(out: str, start_marker: str, end_marker: str) -> str:
    """The source text between two markers, so an assertion can target one function's own body
    rather than matching a string that merely appears somewhere else on the page."""
    start = out.index(start_marker)
    end = out.index(end_marker, start)
    return out[start:end]


def test_video_expand_restore_moves_video_and_control_bar_back() -> None:
    out = html_report("run1", [_passing()])
    restore = _function_body(out, "function vzRestore()", "function vzBuildSteps(player)")
    # A DOM move (appendChild), not a clone, and the player is un-hidden rather than rebuilt.
    assert "home.appendChild(vzActive)" in restore
    assert "home.appendChild(vctl)" in restore
    assert "home.hidden = false" in restore
    # Restoring recomputes the Result view's height bound against the player it just un-hid —
    # `syncResultHeight` also appears elsewhere in report.js (its own definition), so this checks
    # it specifically inside vzRestore's own body, not merely somewhere on the page.
    assert "syncResultHeight(scn)" in restore


def test_video_expand_mount_moves_video_and_control_bar() -> None:
    out = html_report("run1", [_passing()])
    mount = _function_body(out, "function vzMount(player)", "function vzClose()")
    assert "vzVideoSlot.appendChild(v)" in mount
    assert "vzVideoSlot.appendChild(vctl)" in mount
    assert "player.hidden = true" in mount
    # The moved bar's own expand button would find no `.player` ancestor to reopen — hidden rather
    # than left as a dead control.
    assert "x.hidden = true" in mount


def test_video_expand_clone_empties_view_cell_and_strips_group_id() -> None:
    out = html_report("run1", [_passing()])
    # The screenshot/element-tree cell is emptied, not removed (removing it would shift the
    # `.sttbl` grid's later columns — see report.css's `:nth-child` rules).
    assert "evCell.textContent = ''" in out
    # `data-group-id` is stripped from every cloned row so a compact-view fold toggle never
    # reaches the clone (report.js's `.grouptoggle` handler is delegated globally by this
    # attribute).
    assert "clone.removeAttribute('data-group-id')" in out
    assert "sibClone.removeAttribute('data-group-id')" in out


def test_video_expand_clones_companion_rows() -> None:
    out = html_report("run1", [_passing()])
    assert "COMPANION_CLASSES = ['alertrow', 'actrow', 'genrow']" in out


def test_video_expand_tabs_use_their_own_class_not_tab() -> None:
    out = html_report("run1", [_passing()])
    # `.vz-tab`, never `.tab` — `.tab`'s delegated handler assumes an ancestor `.scn`, which the
    # modal (outside every `.scn`) does not have.
    assert "tab.className = 'vz-tab'" in out
    assert "vzTabsEl.querySelectorAll('.vz-tab')" in out


def test_video_expand_escape_defers_to_tv_and_imgz() -> None:
    out = html_report("run1", [_passing()])
    keydown = _function_body(
        out, "if(!vz.classList.contains('open')) return;", "// Custom player chrome"
    )
    assert "tv && tv.classList.contains('open')) return" in keydown
    assert "imgz && imgz.classList.contains('open')) return" in keydown
    assert "vzClose()" in keydown


def test_video_expand_css_present() -> None:
    out = html_report("run1", [_passing()])
    assert ".vz{position:fixed" in out
    assert ".vz-box{" in out
    assert ".vexpand{" in out
    assert ".vz-tab{" in out
    # A fixed step column would squeeze the video to a sliver on a phone-width viewport — the
    # narrow-screen fallback stacks video-then-steps instead.
    assert "@media(max-width:760px){\n .vz-box{flex-direction:column}" in out
