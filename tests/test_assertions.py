"""アサーション評価（DESIGN.md §6.4）のテスト。

シナリオ（§6）→ 解決（§5）→ 判定（§6.4）が純ロジックで閉じることを担保する。
"""

from __future__ import annotations

from simpilot.assertions import evaluate, evaluate_one, passed
from simpilot.drivers import base
from simpilot.scenario import Assertion


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
    _el("submit", "送信", ["button", "notEnabled"]),       # disabled
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
    assert _ok({"exists": {"id": "spinner", "negate": True}})       # 不在を検証 → 不在なので pass
    assert not _ok({"exists": {"id": "home.title", "negate": True}})  # 存在するので fail


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
    assert r.reason  # 失敗理由が入る


def test_ambiguous_state_fails() -> None:
    # 複数一致するセレクタで状態判定 → 一意解決できず失敗（§5 ambiguous）。
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
            _a({"exists": {"id": "spinner"}}),  # これが失敗
        ],
    )
    assert not passed(results)
