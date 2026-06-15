"""Tests for assertion evaluation.

Verify that scenario -> resolve -> assert closes as pure logic.
"""

from __future__ import annotations

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


def test_exists() -> None:
    assert _ok({"exists": {"id": "home.title"}})
    assert not _ok({"exists": {"id": "spinner"}})


def test_exists_negate() -> None:
    assert _ok({"exists": {"id": "spinner", "negate": True}})  # absent, so passes
    assert not _ok({"exists": {"id": "home.title", "negate": True}})  # present, so fails


def test_value() -> None:
    assert _ok({"value": {"sel": {"id": "counter"}, "equals": "3"}})
    assert not _ok({"value": {"sel": {"id": "counter"}, "equals": "4"}})


def test_label_contains_and_matches() -> None:
    assert _ok({"label": {"sel": {"id": "status"}, "contains": "完了"}})
    assert _ok({"label": {"sel": {"id": "status"}, "matches": "完了.*した"}})
    assert not _ok({"label": {"sel": {"id": "status"}, "contains": "失敗"}})


def test_count() -> None:
    assert _ok({"count": {"sel": {"idMatches": "result.row.*"}, "equals": 2}})
    assert not _ok({"count": {"sel": {"idMatches": "result.row.*"}, "equals": 3}})
    assert _ok({"count": {"sel": {"idMatches": "result.row.*"}, "atLeast": 2}})
    assert not _ok({"count": {"sel": {"idMatches": "result.row.*"}, "atMost": 1}})


def test_enabled_disabled() -> None:
    assert _ok({"disabled": {"id": "submit"}})
    assert not _ok({"enabled": {"id": "submit"}})
    assert _ok({"enabled": {"id": "home.title"}})


def test_selected() -> None:
    assert _ok({"selected": {"id": "tab.home"}})
    assert not _ok({"selected": {"id": "home.title"}})


def test_not_found_fails_with_reason() -> None:
    r = evaluate_one(SCREEN, _a({"value": {"sel": {"id": "nope"}, "equals": "x"}}))
    assert not r.ok
    assert r.reason  # a failure reason is set


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
