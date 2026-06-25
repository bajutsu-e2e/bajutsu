"""Tests for assertion evaluation.

Verify that scenario -> resolve -> assert closes as pure logic.
"""

from __future__ import annotations

import pytest
from conftest import el

from bajutsu.assertions import evaluate, evaluate_one, passed
from bajutsu.drivers import base
from bajutsu.scenario import Assertion

SCREEN: list[base.Element] = [
    el("home.title", "ホーム", ["staticText"]),
    el("counter", "カウント", ["staticText"], value="3"),
    el("submit", "送信", ["button", "notEnabled"]),  # disabled
    el("tab.home", "ホームタブ", ["button", "selected"]),
    el("status", "処理完了しました", ["staticText"]),
    el("result.row.1", "A", ["cell"]),
    el("result.row.2", "B", ["cell"]),
]


def _a(data: dict[str, object]) -> Assertion:
    return Assertion.model_validate(data)


def _ok(data: dict[str, object]) -> bool:
    return evaluate_one(SCREEN, _a(data)).ok


# Each assertion kind evaluated against SCREEN: the data and whether it should pass. One row per
# case keeps every kind's matching/non-matching coverage that the per-kind functions had.
@pytest.mark.parametrize(
    ("data", "expect_ok"),
    [
        ({"exists": {"id": "home.title"}}, True),
        ({"exists": {"id": "spinner"}}, False),
        ({"exists": {"id": "spinner", "negate": True}}, True),  # absent + negate, so passes
        ({"exists": {"id": "home.title", "negate": True}}, False),  # present + negate, so fails
        ({"value": {"sel": {"id": "counter"}, "equals": "3"}}, True),
        ({"value": {"sel": {"id": "counter"}, "equals": "4"}}, False),
        ({"label": {"sel": {"id": "status"}, "contains": "完了"}}, True),
        ({"label": {"sel": {"id": "status"}, "matches": "完了.*した"}}, True),
        ({"label": {"sel": {"id": "status"}, "contains": "失敗"}}, False),
        ({"count": {"sel": {"idMatches": "result.row.*"}, "equals": 2}}, True),
        ({"count": {"sel": {"idMatches": "result.row.*"}, "equals": 3}}, False),
        ({"count": {"sel": {"idMatches": "result.row.*"}, "atLeast": 2}}, True),
        ({"count": {"sel": {"idMatches": "result.row.*"}, "atMost": 1}}, False),
        ({"disabled": {"id": "submit"}}, True),
        ({"enabled": {"id": "submit"}}, False),
        ({"enabled": {"id": "home.title"}}, True),
        ({"selected": {"id": "tab.home"}}, True),
        ({"selected": {"id": "home.title"}}, False),
    ],
)
def test_assertion_evaluates(data: dict[str, object], expect_ok: bool) -> None:
    assert _ok(data) is expect_ok


def test_not_found_fails_with_reason() -> None:
    r = evaluate_one(SCREEN, _a({"value": {"sel": {"id": "nope"}, "equals": "x"}}))
    assert not r.ok
    assert r.reason  # a failure reason is set


# --- clipboard read-back (BE-0052) ---


def test_clipboard_equals_matches_the_read_value() -> None:
    r = evaluate_one(SCREEN, _a({"clipboard": {"equals": "COUPON123"}}), clipboard="COUPON123")
    assert r.ok and r.kind == "clipboard"


def test_clipboard_equals_mismatch_fails_with_reason() -> None:
    r = evaluate_one(SCREEN, _a({"clipboard": {"equals": "COUPON123"}}), clipboard="other")
    assert not r.ok
    assert "COUPON123" in r.reason and "other" in r.reason


def test_clipboard_matches_regex() -> None:
    r = evaluate_one(SCREEN, _a({"clipboard": {"matches": r"\d{6}"}}), clipboard="code 482913")
    assert r.ok


def test_clipboard_without_device_control_fails_cleanly() -> None:
    # No clipboard supplied (fake driver / parallel run): a clean not-ok, not a crash — mirrors how
    # the visual assertion degrades when no visual context is provided.
    r = evaluate_one(SCREEN, _a({"clipboard": {"equals": "x"}}), clipboard=None)
    assert not r.ok
    assert "clipboard" in r.reason.lower()


def test_ambiguous_state_fails() -> None:
    # State assertion on a selector that matches multiple -> cannot resolve uniquely.
    r = evaluate_one(SCREEN, _a({"enabled": {"idMatches": "result.row.*"}}))
    assert not r.ok
    assert "件一致" in r.reason


def test_evaluate_and_passed() -> None:
    results = evaluate(
        SCREEN,
        [
            _a({"exists": {"id": "home.title"}}),
            _a({"value": {"sel": {"id": "counter"}, "equals": "3"}}),
        ],
    )
    assert passed(results)

    results = evaluate(
        SCREEN,
        [
            _a({"exists": {"id": "home.title"}}),
            _a({"exists": {"id": "spinner"}}),  # this one fails
        ],
    )
    assert not passed(results)


def test_compile_cache_reuses_compiled_pattern() -> None:
    """_compile caches compiled regex patterns in assertions module."""
    from bajutsu.assertions import _compile

    _compile.cache_clear()
    _compile("foo.*bar")
    _compile("foo.*bar")
    info = _compile.cache_info()
    assert info.hits == 1 and info.misses == 1


# --- visual assertion evaluation ---


def test_visual_assertion_pass(tmp_path):
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    img.save(baselines / "red.png")

    # Simulate a driver screenshot that matches
    screenshot = tmp_path / "screenshot.png"
    img.save(screenshot)

    ctx = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(SCREEN, _a({"visual": {"baseline": "red.png"}}), visual_context=ctx)
    assert result.ok
    assert result.kind == "visual"
    assert result.visual is not None
    assert result.visual.diff_pct == 0.0
    assert result.visual.diff is None  # identical → no diff image
    assert result.visual.actual == "screenshot.png"  # run-dir-relative
    assert result.visual.baseline is not None  # baseline copied into the run dir
    assert (tmp_path / result.visual.baseline).is_file()


def test_visual_assertion_fail(tmp_path):
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(baselines / "red.png")

    screenshot = tmp_path / "screenshot.png"
    Image.new("RGBA", (10, 10), (0, 0, 255, 255)).save(screenshot)

    ctx = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(SCREEN, _a({"visual": {"baseline": "red.png"}}), visual_context=ctx)
    assert not result.ok
    assert "diff" in result.reason
    assert result.visual is not None
    assert result.visual.diff_pct == 100.0  # every pixel differs
    assert result.visual.diff is not None and (tmp_path / result.visual.diff).is_file()
    assert not result.visual.missing


def test_visual_assertion_missing_baseline(tmp_path):
    from bajutsu.assertions import VisualContext

    ctx = VisualContext(
        screenshot_path=tmp_path / "00-home" / "visual-actual.png",
        baselines_dir=tmp_path / "baselines",
        diff_dir=tmp_path / "00-home",
        run_dir=tmp_path,
    )
    result = evaluate_one(SCREEN, _a({"visual": {"baseline": "missing.png"}}), visual_context=ctx)
    assert not result.ok
    assert "baseline not found" in result.reason
    assert result.visual is not None
    assert result.visual.missing
    assert result.visual.baseline is None
    assert result.visual.actual == "00-home/visual-actual.png"  # run-dir-relative
    assert result.visual.baseline_name == "missing.png"


def test_visual_assertion_no_context():
    result = evaluate_one(SCREEN, _a({"visual": {"baseline": "x.png"}}))
    assert not result.ok
    assert "no visual context" in result.reason
