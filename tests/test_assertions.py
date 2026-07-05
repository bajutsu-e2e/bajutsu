"""Tests for assertion evaluation.

Verify that scenario -> resolve -> assert closes as pure logic.
"""

from __future__ import annotations

from pathlib import Path

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


# --- visual assertion schema (BE-0165) ---


def test_visual_match_compare_defaults_to_none():
    a = _a({"visual": {"baseline": "home.png"}})
    assert a.visual is not None
    assert a.visual.compare is None
    assert a.visual.color_tolerance == 0.1
    assert a.visual.antialiasing is True


def test_visual_match_compare_accepts_exact():
    a = _a({"visual": {"baseline": "home.png", "compare": "exact"}})
    assert a.visual is not None
    assert a.visual.compare == "exact"


def test_visual_match_compare_accepts_pixelmatch():
    a = _a({"visual": {"baseline": "home.png", "compare": "pixelmatch"}})
    assert a.visual is not None
    assert a.visual.compare == "pixelmatch"


def test_visual_match_compare_rejects_invalid():
    with pytest.raises(ValueError, match="Input should be"):
        _a({"visual": {"baseline": "home.png", "compare": "ssim"}})


def test_visual_match_color_tolerance_range():
    a = _a({"visual": {"baseline": "x.png", "colorTolerance": 0.5}})
    assert a.visual is not None
    assert a.visual.color_tolerance == 0.5

    with pytest.raises(ValueError, match="less than or equal to 1"):
        _a({"visual": {"baseline": "x.png", "colorTolerance": 1.5}})

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        _a({"visual": {"baseline": "x.png", "colorTolerance": -0.1}})


def test_visual_match_antialiasing_default_true():
    a = _a({"visual": {"baseline": "x.png"}})
    assert a.visual is not None
    assert a.visual.antialiasing is True

    a2 = _a({"visual": {"baseline": "x.png", "antialiasing": False}})
    assert a2.visual is not None
    assert a2.visual.antialiasing is False


def test_visual_match_exact_with_pixelmatch_tolerances_rejected():
    with pytest.raises(Exception, match="pixelmatch"):
        _a({"visual": {"baseline": "x.png", "compare": "exact", "colorTolerance": 0.5}})


def test_visual_match_none_compare_with_tolerances_ok():
    a = _a({"visual": {"baseline": "x.png", "colorTolerance": 0.5}})
    assert a.visual is not None
    assert a.visual.color_tolerance == 0.5


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


def test_visual_evidence_records_engine(tmp_path):
    """VisualEvidence.engine reflects the resolved compare engine."""
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    img.save(baselines / "red.png")
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
    assert result.visual is not None
    assert result.visual.engine == "exact"


def test_visual_evidence_records_pixelmatch(tmp_path):
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    img.save(baselines / "red.png")
    screenshot = tmp_path / "screenshot.png"
    img.save(screenshot)

    ctx = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN,
        _a({"visual": {"baseline": "red.png", "compare": "pixelmatch"}}),
        visual_context=ctx,
    )
    assert result.ok
    assert result.visual is not None
    assert result.visual.engine == "pixelmatch"


def test_visual_context_default_compare_fallback(tmp_path):
    """When the assertion has no compare, VisualContext.default_compare is used."""
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    img.save(baselines / "red.png")
    screenshot = tmp_path / "screenshot.png"
    img.save(screenshot)

    ctx = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
        default_compare="pixelmatch",
    )
    result = evaluate_one(
        SCREEN,
        _a({"visual": {"baseline": "red.png"}}),
        visual_context=ctx,
    )
    assert result.ok
    assert result.visual is not None
    assert result.visual.engine == "pixelmatch"


# --- golden element-tree assertion (BE-0006) ---


def test_golden_model_accepts_valid_path() -> None:
    a = _a({"golden": {"path": "goldens/controls.json"}})
    assert a.golden is not None
    assert a.golden.path == "goldens/controls.json"


def test_golden_model_rejects_empty_path() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="string_too_short"):
        _a({"golden": {"path": ""}})


def test_golden_assertion_pass(tmp_path: Path) -> None:
    import json

    from bajutsu.assertions import GoldenContext

    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    golden_data = {
        "ctrl.toggle": {
            "identifier": "ctrl.toggle",
            "label": "Toggle",
            "traits": ["switch"],
            "value": "1",
            "frame": [10.0, 20.0, 50.0, 30.0],
        }
    }
    (golden_dir / "controls.json").write_text(json.dumps(golden_data), encoding="utf-8")

    elements: list[base.Element] = [
        el("ctrl.toggle", "Toggle", ["switch"], value="1", frame=(10.0, 20.0, 50.0, 30.0)),
    ]
    ctx = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one(elements, _a({"golden": {"path": "controls.json"}}), golden_context=ctx)
    assert result.ok
    assert result.kind == "golden"


def test_golden_assertion_mismatch_fails(tmp_path: Path) -> None:
    import json

    from bajutsu.assertions import GoldenContext

    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    golden_data = {
        "ctrl.toggle": {
            "identifier": "ctrl.toggle",
            "label": "Toggle",
            "traits": ["switch"],
            "value": "1",
            "frame": [10.0, 20.0, 50.0, 30.0],
        }
    }
    (golden_dir / "controls.json").write_text(json.dumps(golden_data), encoding="utf-8")

    elements: list[base.Element] = [
        el("ctrl.toggle", "Switch", ["button"], value="0", frame=(10.0, 20.0, 50.0, 30.0)),
    ]
    ctx = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one(elements, _a({"golden": {"path": "controls.json"}}), golden_context=ctx)
    assert not result.ok
    assert "ctrl.toggle" in result.reason


def test_golden_assertion_missing_element_fails(tmp_path: Path) -> None:
    import json

    from bajutsu.assertions import GoldenContext

    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    golden_data = {
        "ctrl.toggle": {
            "identifier": "ctrl.toggle",
            "label": "Toggle",
            "traits": ["switch"],
            "value": "1",
            "frame": [10.0, 20.0, 50.0, 30.0],
        }
    }
    (golden_dir / "controls.json").write_text(json.dumps(golden_data), encoding="utf-8")

    ctx = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one([], _a({"golden": {"path": "controls.json"}}), golden_context=ctx)
    assert not result.ok
    assert "missing" in result.reason.lower()


def test_golden_assertion_no_context_fails() -> None:
    result = evaluate_one(SCREEN, _a({"golden": {"path": "controls.json"}}))
    assert not result.ok
    assert "no golden context" in result.reason


def test_golden_assertion_file_not_found_fails(tmp_path: Path) -> None:
    from bajutsu.assertions import GoldenContext

    ctx = GoldenContext(goldens_dir=tmp_path)
    result = evaluate_one(SCREEN, _a({"golden": {"path": "nonexistent.json"}}), golden_context=ctx)
    assert not result.ok


def test_golden_path_traversal_rejected(tmp_path: Path) -> None:
    from bajutsu.assertions import GoldenContext

    ctx = GoldenContext(goldens_dir=tmp_path)
    result = evaluate_one(SCREEN, _a({"golden": {"path": "../../etc/passwd"}}), golden_context=ctx)
    assert not result.ok
    assert "escapes" in result.reason


def test_golden_via_evaluate(tmp_path: Path) -> None:
    """Golden assertions work through the batch evaluate() path too."""
    import json

    from bajutsu.assertions import GoldenContext

    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    golden_data = {
        "ctrl.toggle": {
            "identifier": "ctrl.toggle",
            "label": "Toggle",
            "traits": ["switch"],
            "value": "1",
            "frame": [10.0, 20.0, 50.0, 30.0],
        }
    }
    (golden_dir / "controls.json").write_text(json.dumps(golden_data), encoding="utf-8")

    elements: list[base.Element] = [
        el("ctrl.toggle", "Toggle", ["switch"], value="1", frame=(10.0, 20.0, 50.0, 30.0)),
    ]
    ctx = GoldenContext(goldens_dir=golden_dir)
    results = evaluate(
        elements,
        [
            _a({"exists": {"id": "ctrl.toggle"}}),
            _a({"golden": {"path": "controls.json"}}),
        ],
        golden_context=ctx,
    )
    assert passed(results)
    assert results[1].kind == "golden"
