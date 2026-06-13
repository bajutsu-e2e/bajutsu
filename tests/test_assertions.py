"""Tests for assertion evaluation.

Verify that scenario -> resolve -> assert closes as pure logic.
"""

from __future__ import annotations

from bajutsu.assertions import evaluate, evaluate_one, passed
from bajutsu.drivers import base
from bajutsu.scenario import Assertion


def _el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


SCREEN: list[base.Element] = [
    _el("home.title", "ホーム", ["staticText"]),
    _el("counter", "カウント", ["staticText"], value="3"),
    _el("submit", "送信", ["button", "notEnabled"]),  # disabled
    _el("tab.home", "ホームタブ", ["button", "selected"]),
    _el("status", "処理完了しました", ["staticText"]),
    _el("result.row.1", "A", ["cell"]),
    _el("result.row.2", "B", ["cell"]),
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
