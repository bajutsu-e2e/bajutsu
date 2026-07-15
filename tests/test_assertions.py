"""Tests for assertion evaluation.

Verify that scenario -> resolve -> assert closes as pure logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import el

from bajutsu.assertions import EvalContext, evaluate, evaluate_one, passed
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


def test_public_surface_reexported_from_package_root() -> None:
    """The package split (BE-0250) keeps every public name importable from `bajutsu.assertions`."""
    import bajutsu.assertions as pkg

    for name in (
        "AssertionResult",
        "EvalContext",
        "GoldenContext",
        "SchemaContext",
        "VisualContext",
        "VisualEvidence",
        "count_matching",
        "evaluate",
        "evaluate_one",
        "match_request",
        "passed",
        "request_label",
    ):
        assert hasattr(pkg, name), name
        assert name in pkg.__all__, name


def test_submodules_import_without_a_cycle() -> None:
    """Each seam module loads on its own — the leaf `_common` keeps the split acyclic (BE-0250)."""
    import importlib

    for mod in ("_common", "network", "visual", "schema", "evaluate"):
        assert importlib.import_module(f"bajutsu.assertions.{mod}") is not None


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


# --- Evaluator registry (BE-0250 Unit 3) ---


def test_evaluator_registry_covers_every_kind_exactly() -> None:
    """The dispatch registry has one evaluator per assertion kind — no missing kind, no stray
    entry — so `evaluate_one`'s lookup can never fall through for a valid assertion (BE-0250)."""
    from bajutsu.assertions.evaluate import _EVALUATORS
    from bajutsu.scenario import ASSERTION_KINDS

    assert set(_EVALUATORS) == set(ASSERTION_KINDS)


# --- Assertion-kind derivation (BE-0250 Unit 4) ---


def test_assertion_kinds_derived_from_model() -> None:
    """`ASSERTION_KINDS` is derived from the `Assertion` model — every field except the
    non-kind provenance `from_` — so a new kind is a single field edit, not also a parallel
    hand-maintained tuple (BE-0250 Unit 4)."""
    from bajutsu.scenario import ASSERTION_KINDS

    assert tuple(f for f in Assertion.model_fields if f != "from_") == ASSERTION_KINDS
    assert "from_" not in ASSERTION_KINDS


# --- EvalContext bundling (BE-0250 Unit 2) ---


def test_eval_context_is_frozen() -> None:
    """EvalContext is a frozen value — a run cannot mutate a shared context (BE-0250 Unit 2)."""
    import dataclasses

    ctx = EvalContext(clipboard="x")
    assert (ctx.visual, ctx.schema, ctx.golden, ctx.clipboard) == (None, None, None, "x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.clipboard = "y"  # type: ignore[misc]


def test_eval_context_delivers_each_field_to_its_kind(tmp_path: Path) -> None:
    """A single EvalContext routes clipboard and golden to their respective evaluators (BE-0250)."""
    import json

    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    entry = {
        "identifier": "c.toggle",
        "label": "T",
        "traits": ["switch"],
        "value": "1",
        "frame": [10.0, 20.0, 50.0, 30.0],
    }
    (golden_dir / "controls.json").write_text(json.dumps({"c.toggle": entry}), encoding="utf-8")
    from bajutsu.assertions import GoldenContext

    ctx = EvalContext(golden=GoldenContext(goldens_dir=golden_dir), clipboard="COUPON123")
    screen = [el("c.toggle", "T", ["switch"], value="1", frame=(10.0, 20.0, 50.0, 30.0))]
    assert evaluate_one(screen, _a({"clipboard": {"equals": "COUPON123"}}), ctx=ctx).ok
    assert evaluate_one(screen, _a({"golden": {"path": "controls.json"}}), ctx=ctx).ok


# --- clipboard read-back (BE-0052) ---


def test_clipboard_equals_matches_the_read_value() -> None:
    r = evaluate_one(
        SCREEN, _a({"clipboard": {"equals": "COUPON123"}}), ctx=EvalContext(clipboard="COUPON123")
    )
    assert r.ok and r.kind == "clipboard"


def test_clipboard_equals_mismatch_fails_with_reason() -> None:
    r = evaluate_one(
        SCREEN, _a({"clipboard": {"equals": "COUPON123"}}), ctx=EvalContext(clipboard="other")
    )
    assert not r.ok
    assert "COUPON123" in r.reason and "other" in r.reason


def test_clipboard_matches_regex() -> None:
    r = evaluate_one(
        SCREEN, _a({"clipboard": {"matches": r"\d{6}"}}), ctx=EvalContext(clipboard="code 482913")
    )
    assert r.ok


def test_clipboard_without_device_control_fails_cleanly() -> None:
    # No clipboard supplied (fake driver / parallel run): a clean not-ok, not a crash — mirrors how
    # the visual assertion degrades when no visual context is provided.
    r = evaluate_one(SCREEN, _a({"clipboard": {"equals": "x"}}), ctx=EvalContext(clipboard=None))
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
    from bajutsu.assertions._common import _compile

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

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN, _a({"visual": {"baseline": "red.png"}}), ctx=EvalContext(visual=vc)
    )
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

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN, _a({"visual": {"baseline": "red.png"}}), ctx=EvalContext(visual=vc)
    )
    assert not result.ok
    assert "diff" in result.reason
    assert result.visual is not None
    assert result.visual.diff_pct == 100.0  # every pixel differs
    assert result.visual.diff is not None and (tmp_path / result.visual.diff).is_file()
    assert not result.visual.missing


def test_visual_assertion_missing_baseline(tmp_path):
    from bajutsu.assertions import VisualContext

    vc = VisualContext(
        screenshot_path=tmp_path / "00-home" / "visual-actual.png",
        baselines_dir=tmp_path / "baselines",
        diff_dir=tmp_path / "00-home",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN, _a({"visual": {"baseline": "missing.png"}}), ctx=EvalContext(visual=vc)
    )
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


def test_visual_pixelmatch_fields_with_resolved_exact_fails(tmp_path):
    """Explicit pixelmatch fields + resolved engine exact → clean failure."""
    from PIL import Image

    from bajutsu.assertions import VisualContext

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    img.save(baselines / "red.png")
    screenshot = tmp_path / "screenshot.png"
    img.save(screenshot)

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
        default_compare="exact",
    )
    result = evaluate_one(
        SCREEN,
        _a({"visual": {"baseline": "red.png", "colorTolerance": 0.5}}),
        ctx=EvalContext(visual=vc),
    )
    assert not result.ok
    assert "resolved engine is 'exact'" in result.reason


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

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN, _a({"visual": {"baseline": "red.png"}}), ctx=EvalContext(visual=vc)
    )
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

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )
    result = evaluate_one(
        SCREEN,
        _a({"visual": {"baseline": "red.png", "compare": "pixelmatch"}}),
        ctx=EvalContext(visual=vc),
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

    vc = VisualContext(
        screenshot_path=screenshot,
        baselines_dir=baselines,
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
        default_compare="pixelmatch",
    )
    result = evaluate_one(
        SCREEN,
        _a({"visual": {"baseline": "red.png"}}),
        ctx=EvalContext(visual=vc),
    )
    assert result.ok
    assert result.visual is not None
    assert result.visual.engine == "pixelmatch"


# --- element-scoped visual & selector masking (BE-0171) ---


def _framed_screen() -> list[base.Element]:
    """A screen whose full-screen root fixes the point-space size at 100x100.

    The card sits inside it; the clock overlaps the top-left; the badge nests inside the card.
    Frames are in points — element scoping/masking converts them to screenshot pixels via the
    screenshot/point scale.
    """
    return [
        el("root", frame=(0.0, 0.0, 100.0, 100.0)),
        el("card", "Summary", ["staticText"], frame=(10.0, 10.0, 40.0, 30.0)),
        el("clock", "last updated", ["staticText"], frame=(0.0, 0.0, 50.0, 8.0)),
        el("badge", "3", ["staticText"], frame=(15.0, 15.0, 10.0, 10.0)),
    ]


def _vc(tmp_path: Path, screenshot: Path):
    from bajutsu.assertions import VisualContext

    return VisualContext(
        screenshot_path=screenshot,
        baselines_dir=tmp_path / "baselines",
        diff_dir=tmp_path / "diffs",
        run_dir=tmp_path,
    )


def _paint(img, box: tuple[int, int, int, int], color: tuple[int, int, int, int]) -> None:
    x, y, w, h = box
    for px in range(x, x + w):
        for py in range(y, y + h):
            img.putpixel((px, py), color)


def test_visual_element_scoped_pass(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    # Baseline is the element crop (40x30), not the whole screen.
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    # Actual full screenshot: red, with the card region matching the green baseline.
    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, shot)),
    )
    assert r.ok
    assert r.visual is not None
    assert r.visual.element_scoped is True
    assert r.visual.diff_pct == 0.0
    # The recorded actual is the element crop (40x30), so `approve` promotes the crop.
    actual_size = Image.open(tmp_path / r.visual.actual).size
    assert actual_size == (40, 30)


def test_visual_element_scoped_missing_baseline_reports_the_crop(tmp_path: Path) -> None:
    """On the first run (no baseline) the reported actual is the element crop, so the first
    `approve` stores an element-sized baseline — not the whole screen."""
    from PIL import Image

    (tmp_path / "baselines").mkdir()  # empty: no baseline yet
    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, shot)),
    )
    assert not r.ok
    assert r.visual is not None
    assert r.visual.missing is True
    assert r.visual.element_scoped is True
    # The captured actual is the element crop (40x30), so approving it yields an element baseline.
    actual_size = Image.open(tmp_path / r.visual.actual).size
    assert actual_size == (40, 30)


def test_visual_element_scoped_ignores_unrelated_change(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    # The card matches, but a large unrelated region differs — whole-screen would fail.
    actual = Image.new("RGBA", (100, 100), (0, 0, 255, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, shot)),
    )
    assert r.ok


def test_visual_element_scoped_fail(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 0, 255, 255))  # card differs from baseline
    shot = tmp_path / "shot.png"
    actual.save(shot)

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, shot)),
    )
    assert not r.ok
    assert r.visual is not None
    assert r.visual.diff_pct == 100.0
    assert r.visual.diff is not None and (tmp_path / r.visual.diff).is_file()


def test_visual_element_ambiguous_fails(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(tmp_path / "shot.png")

    # Two staticText elements match — an ambiguous scope must fail, not crop the first.
    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"traits": ["staticText"]}}}),
        ctx=EvalContext(visual=_vc(tmp_path, tmp_path / "shot.png")),
    )
    assert not r.ok
    assert r.reason.startswith("element ")  # a resolution failure, not a pixel diff
    assert r.visual is not None and r.visual.diff_pct is None  # never compared


def test_visual_element_not_found_fails(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(tmp_path / "shot.png")

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "nope"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, tmp_path / "shot.png")),
    )
    assert not r.ok


def test_visual_element_empty_frame_fails_cleanly(tmp_path: Path) -> None:
    """A zero-area element frame fails the assertion instead of crashing Pillow on an empty crop."""
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(tmp_path / "shot.png")

    screen = [
        el("root", frame=(0.0, 0.0, 100.0, 100.0)),
        el("collapsed", "hidden", ["staticText"], frame=(10.0, 10.0, 0.0, 0.0)),  # zero area
    ]
    r = evaluate_one(
        screen,
        _a({"visual": {"baseline": "card.png", "element": {"id": "collapsed"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, tmp_path / "shot.png")),
    )
    assert not r.ok
    assert "empty frame" in r.reason


def test_visual_element_scoped_scale_factor(tmp_path: Path) -> None:
    """A 2x screenshot: point frames must be scaled to pixels before cropping."""
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    # card is (10,10,40,30) points → (20,20,80,60) pixels at 2x.
    Image.new("RGBA", (80, 60), (0, 255, 0, 255)).save(baselines / "card.png")
    actual = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
    _paint(actual, (20, 20, 80, 60), (0, 255, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    r = evaluate_one(
        _framed_screen(),
        _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}),
        ctx=EvalContext(visual=_vc(tmp_path, shot)),
    )
    assert r.ok
    assert r.visual is not None
    actual_size = Image.open(tmp_path / r.visual.actual).size
    assert actual_size == (80, 60)


def test_visual_selector_mask_hides_dynamic_element(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(baselines / "home.png")
    # Actual differs only inside the clock's frame (0,0,50,8).
    actual = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    _paint(actual, (0, 0, 50, 8), (255, 0, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    masked = _a({"visual": {"baseline": "home.png", "exclude": [{"selector": {"id": "clock"}}]}})
    r = evaluate_one(_framed_screen(), masked, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert r.ok
    assert r.visual is not None
    assert r.visual.masked_selectors  # provenance records the mask

    # Without the mask the same screen fails — proving the mask did the work.
    bare = _a({"visual": {"baseline": "home.png"}})
    r2 = evaluate_one(_framed_screen(), bare, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert not r2.ok


def test_visual_selector_mask_not_found_is_noop(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(baselines / "home.png")
    actual = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    _paint(actual, (0, 0, 50, 8), (255, 0, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)

    # A selector matching nothing masks nothing — the real diff survives and fails.
    a = _a({"visual": {"baseline": "home.png", "exclude": [{"selector": {"id": "ghost"}}]}})
    r = evaluate_one(_framed_screen(), a, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert not r.ok


def test_visual_selector_mask_ambiguous_fails(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(baselines / "home.png")
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(tmp_path / "shot.png")

    a = _a(
        {"visual": {"baseline": "home.png", "exclude": [{"selector": {"traits": ["staticText"]}}]}}
    )
    r = evaluate_one(
        _framed_screen(), a, ctx=EvalContext(visual=_vc(tmp_path, tmp_path / "shot.png"))
    )
    assert not r.ok
    assert r.reason.startswith("exclude selector ")  # ambiguous mask fails, never masks first match


def test_visual_element_scoped_with_selector_mask(tmp_path: Path) -> None:
    """A crop plus a mask inside it — the mask must translate into crop-local coordinates."""
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    # The card matches the baseline except the badge region (15,15,10,10) inside it.
    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    _paint(actual, (15, 15, 10, 10), (0, 0, 255, 255))  # dynamic badge differs
    shot = tmp_path / "shot.png"
    actual.save(shot)

    a = _a(
        {
            "visual": {
                "baseline": "card.png",
                "element": {"id": "card"},
                "exclude": [{"selector": {"id": "badge"}}],
            }
        }
    )
    r = evaluate_one(_framed_screen(), a, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert r.ok


def test_visual_element_scoped_mask_straddling_crop_edge(tmp_path: Path) -> None:
    """A mask that overlaps the crop boundary is clipped into crop-local coordinates, not dropped."""
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (40, 30), (0, 255, 0, 255)).save(baselines / "card.png")
    # The card (10,10,40,30) matches, except a region that a straddling element covers.
    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    _paint(actual, (10, 10, 15, 15), (0, 0, 255, 255))  # differs inside the card's top-left
    shot = tmp_path / "shot.png"
    actual.save(shot)

    # The masking element straddles the card's top-left corner (starts outside the crop).
    screen = [
        el("root", frame=(0.0, 0.0, 100.0, 100.0)),
        el("card", "Summary", ["cell"], frame=(10.0, 10.0, 40.0, 30.0)),
        el("straddle", "overlay", ["staticText"], frame=(5.0, 5.0, 20.0, 20.0)),
    ]
    a = _a(
        {
            "visual": {
                "baseline": "card.png",
                "element": {"id": "card"},
                "exclude": [{"selector": {"id": "straddle"}}],
            }
        }
    )
    r = evaluate_one(screen, a, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert r.ok


def test_visual_mixed_rectangle_and_selector_mask(tmp_path: Path) -> None:
    from PIL import Image

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(baselines / "home.png")
    actual = Image.new("RGBA", (100, 100), (0, 255, 0, 255))
    _paint(actual, (0, 0, 50, 8), (255, 0, 0, 255))  # clock (masked by selector)
    _paint(actual, (80, 90, 20, 10), (255, 0, 0, 255))  # corner (masked by rectangle)
    shot = tmp_path / "shot.png"
    actual.save(shot)

    a = _a(
        {
            "visual": {
                "baseline": "home.png",
                "exclude": [
                    {"selector": {"id": "clock"}},
                    {"x": 80, "y": 90, "w": 20, "h": 10},
                ],
            }
        }
    )
    r = evaluate_one(_framed_screen(), a, ctx=EvalContext(visual=_vc(tmp_path, shot)))
    assert r.ok


# --- visual _eval_visual helpers (unit) ---


def test_prepare_visual_comparison_whole_screen_is_a_passthrough(tmp_path: Path) -> None:
    """No element / no selector mask: preprocessing leaves the whole screenshot as the actual."""
    from PIL import Image

    from bajutsu.assertions.visual import _prepare_visual_comparison

    shot = tmp_path / "shot.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(shot)
    a = _a({"visual": {"baseline": "home.png"}}).visual
    assert a is not None
    prepared = _prepare_visual_comparison(_vc(tmp_path, shot), a, _framed_screen(), "home.png")

    # A whole-screen comparison: no crop, no scale, the actual is the untouched screenshot.
    from bajutsu.assertions.visual import _Prepared

    assert isinstance(prepared, _Prepared)
    assert prepared.crop is None
    assert prepared.scale is None
    assert prepared.compare_actual == shot
    assert prepared.actual_rel == "shot.png"


def test_prepare_visual_comparison_crops_to_the_element(tmp_path: Path) -> None:
    """An element-scoped comparison writes the crop and reports it as the actual (40x30 card)."""
    from PIL import Image

    from bajutsu.assertions.visual import _prepare_visual_comparison, _Prepared

    actual = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    _paint(actual, (10, 10, 40, 30), (0, 255, 0, 255))
    shot = tmp_path / "shot.png"
    actual.save(shot)
    a = _a({"visual": {"baseline": "card.png", "element": {"id": "card"}}}).visual
    assert a is not None
    prepared = _prepare_visual_comparison(_vc(tmp_path, shot), a, _framed_screen(), "card.png")

    assert isinstance(prepared, _Prepared)
    assert prepared.crop is not None
    assert prepared.scale == (1.0, 1.0)  # 100px screenshot over 100pt screen
    # The crop is written to diff_dir/actual-card.png and is the 40x30 element region.
    with Image.open(prepared.compare_actual) as cropped:
        crop_size = cropped.size
    assert crop_size == (40, 30)
    assert prepared.actual_rel == "diffs/actual-card.png"


def test_prepare_visual_comparison_element_not_found_returns_result(tmp_path: Path) -> None:
    """An unresolvable element scope short-circuits to a failing AssertionResult, not a crop."""
    from PIL import Image

    from bajutsu.assertions import AssertionResult
    from bajutsu.assertions.visual import _prepare_visual_comparison

    shot = tmp_path / "shot.png"
    Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(shot)
    a = _a({"visual": {"baseline": "card.png", "element": {"id": "nope"}}}).visual
    assert a is not None
    out = _prepare_visual_comparison(_vc(tmp_path, shot), a, _framed_screen(), "card.png")

    assert isinstance(out, AssertionResult)
    assert not out.ok
    assert out.reason.startswith("element ")
    assert out.visual is not None and out.visual.element_scoped is True


def test_resolve_masks_selector_and_rectangle(tmp_path: Path) -> None:
    """Selector masks resolve to pixel rectangles (with provenance); plain rectangles pass through."""
    from PIL import Image

    from bajutsu.assertions import AssertionResult
    from bajutsu.assertions.visual import _resolve_masks
    from bajutsu.scenario import ExcludeRegion

    shot = tmp_path / "shot.png"
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(shot)
    a = _a(
        {
            "visual": {
                "baseline": "home.png",
                "exclude": [
                    {"selector": {"id": "clock"}},
                    {"x": 80, "y": 90, "w": 20, "h": 10},
                ],
            }
        }
    ).visual
    assert a is not None
    out = _resolve_masks(
        a, _framed_screen(), scale=(1.0, 1.0), crop=None, detail="visual ≈ home.png"
    )

    assert not isinstance(out, AssertionResult)
    masks, masked_selectors = out
    # clock (0,0,50,8) resolved to a pixel rect; the explicit rectangle passed through unchanged.
    assert ExcludeRegion(x=0, y=0, w=50, h=8) in masks
    assert ExcludeRegion(x=80, y=90, w=20, h=10) in masks
    assert masked_selectors == ["id='clock'"]


def test_resolve_masks_ambiguous_selector_returns_result(tmp_path: Path) -> None:
    """An ambiguous mask selector fails the assertion rather than masking the first match."""
    from bajutsu.assertions import AssertionResult
    from bajutsu.assertions.visual import _resolve_masks

    a = _a(
        {"visual": {"baseline": "home.png", "exclude": [{"selector": {"traits": ["staticText"]}}]}}
    ).visual
    assert a is not None
    out = _resolve_masks(
        a, _framed_screen(), scale=(1.0, 1.0), crop=None, detail="visual ≈ home.png"
    )

    assert isinstance(out, AssertionResult)
    assert not out.ok
    assert out.reason.startswith("exclude selector ")


def test_resolve_masks_translates_into_crop_local_coordinates(tmp_path: Path) -> None:
    """When element-scoped, masks are shifted into the crop's local coordinate space."""
    from bajutsu.assertions.visual import _resolve_masks
    from bajutsu.scenario import ExcludeRegion

    a = _a({"visual": {"baseline": "card.png", "exclude": [{"selector": {"id": "badge"}}]}}).visual
    assert a is not None
    # crop origin (10,10): badge at pixel (15,15) becomes crop-local (5,5).
    crop = ExcludeRegion(x=10, y=10, w=40, h=30)
    out = _resolve_masks(a, _framed_screen(), scale=(1.0, 1.0), crop=crop, detail="d")

    masks, _ = out  # type: ignore[misc]
    assert ExcludeRegion(x=5, y=5, w=10, h=10) in masks


def test_resolve_baselines_copies_the_baseline_and_prepares_the_diff_path(tmp_path: Path) -> None:
    """Baseline I/O: the baseline is copied into the run dir and the diff path is prepared."""
    from PIL import Image

    from bajutsu.assertions.visual import _resolve_baselines

    baselines = tmp_path / "baselines"
    baselines.mkdir()
    Image.new("RGBA", (10, 10), (0, 255, 0, 255)).save(baselines / "home.png")
    ctx = _vc(tmp_path, tmp_path / "shot.png")

    baseline_copy, diff_path = _resolve_baselines(ctx, baselines / "home.png", "home.png")

    assert baseline_copy == tmp_path / "diffs" / "baseline-home.png"
    assert baseline_copy.is_file()  # copied into the run dir so the report is self-contained
    assert diff_path == tmp_path / "diffs" / "diff-home.png"
    assert (tmp_path / "diffs").is_dir()  # diff dir was created


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
    gc = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one(
        elements, _a({"golden": {"path": "controls.json"}}), ctx=EvalContext(golden=gc)
    )
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
    gc = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one(
        elements, _a({"golden": {"path": "controls.json"}}), ctx=EvalContext(golden=gc)
    )
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

    gc = GoldenContext(goldens_dir=golden_dir)
    result = evaluate_one([], _a({"golden": {"path": "controls.json"}}), ctx=EvalContext(golden=gc))
    assert not result.ok
    assert "missing" in result.reason.lower()


def test_golden_assertion_no_context_fails() -> None:
    result = evaluate_one(SCREEN, _a({"golden": {"path": "controls.json"}}))
    assert not result.ok
    assert "no golden context" in result.reason


def test_golden_assertion_file_not_found_fails(tmp_path: Path) -> None:
    from bajutsu.assertions import GoldenContext

    gc = GoldenContext(goldens_dir=tmp_path)
    result = evaluate_one(
        SCREEN, _a({"golden": {"path": "nonexistent.json"}}), ctx=EvalContext(golden=gc)
    )
    assert not result.ok


def test_golden_path_traversal_rejected(tmp_path: Path) -> None:
    from bajutsu.assertions import GoldenContext

    gc = GoldenContext(goldens_dir=tmp_path)
    result = evaluate_one(
        SCREEN, _a({"golden": {"path": "../../etc/passwd"}}), ctx=EvalContext(golden=gc)
    )
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
    gc = GoldenContext(goldens_dir=golden_dir)
    results = evaluate(
        elements,
        [
            _a({"exists": {"id": "ctrl.toggle"}}),
            _a({"golden": {"path": "controls.json"}}),
        ],
        ctx=EvalContext(golden=gc),
    )
    assert passed(results)
    assert results[1].kind == "golden"
