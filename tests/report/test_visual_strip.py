"""Tests for the HTML report visual-regression assertion strip."""

from __future__ import annotations

from pathlib import Path

from bajutsu.orchestrator import RunResult
from bajutsu.report import html_report


def test_assert_parts_visual() -> None:
    from bajutsu.report import _assert_parts

    kind, target, comp = _assert_parts(
        {"visual": {"baseline": "home.png", "threshold": 0.5, "exclude": [{"x": 0}, {"x": 1}]}}
    )
    assert kind == "visual"
    assert ("str", "home.png") in target
    assert any(t == "num" and "0.5" in v for t, v in comp)  # threshold token
    assert any("2 excluded" in v for _, v in comp)  # exclude-region count


def test_html_visual_assertion_strip(tmp_path: Path) -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    ev = VisualEvidence(
        baseline_name="home.png",
        actual="00-s1/visual-actual.png",
        baseline="00-s1/baseline-home.png",
        diff="00-s1/diff-home.png",
        diff_pct=2.5,
    )
    ar = AssertionResult(False, "visual", "visual ≈ home.png", "diff 2.5%", visual=ev)
    r = RunResult(scenario="s1", ok=False, steps=[], expect_results=[ar], artifacts=[])
    definition = {
        "name": "s1",
        "steps": [],
        "expect": [{"visual": {"baseline": "home.png", "threshold": 0.5}}],
    }
    out = html_report("runV", [r], tmp_path, definitions=[definition])
    assert "data-run-id='runV'" in out
    assert 'src="00-s1/baseline-home.png"' in out  # comparator base layer
    assert 'src="00-s1/visual-actual.png"' in out  # comparator overlay
    assert 'src="00-s1/diff-home.png"' in out  # precomputed pixel-diff layer
    # the swipe / onion / mix-blend comparator with its modes
    assert 'class="vcmp mode-swipe"' in out
    for m in ("swipe", "onion", "blend", "diff"):
        assert f'data-mode="{m}"' in out
    assert "Approve as baseline" in out
    assert 'data-baseline="home.png"' in out
    assert 'data-sid="00-s1"' in out
    assert "diff 2.50%" in out


def test_html_visual_pass_has_comparator_without_diff_mode(tmp_path: Path) -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    # A passing visual check: baseline ≈ actual, no diff image → comparator but no Diff/Approve.
    ev = VisualEvidence(
        baseline_name="home.png",
        actual="00-s1/visual-actual.png",
        baseline="00-s1/baseline-home.png",
        diff=None,
        diff_pct=0.0,
    )
    ar = AssertionResult(True, "visual", "visual ≈ home.png", "", visual=ev)
    r = RunResult(scenario="s1", ok=True, steps=[], expect_results=[ar], artifacts=[])
    out = html_report("runP", [r], tmp_path)
    assert 'class="vcmp mode-swipe"' in out
    assert 'data-mode="blend"' in out
    assert 'data-mode="diff"' not in out  # no diff image → no Diff mode
    assert "Approve as baseline" not in out  # a passing check is not approvable


def test_html_visual_missing_baseline_no_approve_is_offered() -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    ev = VisualEvidence(baseline_name="home.png", actual="00-s1/visual-actual.png", missing=True)
    ar = AssertionResult(False, "visual", "visual ≈ home.png", "baseline not found", visual=ev)
    r = RunResult(scenario="s1", ok=False, steps=[], expect_results=[ar], artifacts=[])
    out = html_report("runV", [r])
    assert "no baseline yet" in out
    assert "Approve as baseline" in out  # missing baseline is approvable too
