"""アサーション評価（DESIGN.md §6.4）。

`expect` / `assert` のリストを `query()` 結果（list[Element]）に対して機械評価する。
リスト内は AND、1 つでも失敗ならステップ失敗。AI は関与しない（機械チェックのみ。§3.1）。

評価は総関数（例外を投げず結果を返す）にしてレポート（manifest）へそのまま載せる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from simpilot.drivers import base
from simpilot.scenario import Assertion, CountMatch, Exists, Selector, TextMatch


@dataclass(frozen=True)
class AssertionResult:
    ok: bool
    kind: str
    detail: str       # 何を検証したか（レポート向け）
    reason: str = ""  # 失敗理由（ok のとき空）


def _sel_str(sel: Selector) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in sel.as_selector().items())


def _resolve_one(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """単一要素を解決。失敗時は (None, 理由)。曖昧・不在はアサーション失敗として扱う。"""
    try:
        return base.resolve_unique(elements, sel.as_selector()), ""
    except base.SelectorError as e:
        return None, str(e)


def _eval_exists(elements: list[base.Element], a: Exists) -> AssertionResult:
    found = len(base.find_all(elements, a.sel.as_selector())) >= 1
    ok = found != a.negate
    want = "不在" if a.negate else "存在"
    reason = "" if ok else f"{want}を期待したが{'存在' if found else '不在'}"
    return AssertionResult(ok, "exists", f"{want}: {_sel_str(a.sel)}", reason)


def _eval_text(elements: list[base.Element], kind: str, a: TextMatch) -> AssertionResult:
    el, err = _resolve_one(elements, a.sel)
    detail_base = f"{kind}: {_sel_str(a.sel)}"
    if el is None:
        return AssertionResult(False, kind, detail_base, err)
    actual = el["value"] if kind == "value" else el["label"]
    op, expected = _text_op(a)
    ok = _text_cmp(actual, op, expected)
    reason = "" if ok else f"{op}={expected!r} を期待したが actual={actual!r}"
    return AssertionResult(ok, kind, f"{kind} {op}={expected!r}: {_sel_str(a.sel)}", reason)


def _text_op(a: TextMatch) -> tuple[str, str]:
    if a.equals is not None:
        return "equals", a.equals
    if a.contains is not None:
        return "contains", a.contains
    assert a.matches is not None  # scenario 検証で 1 つ保証済み（§6.4）
    return "matches", a.matches


def _text_cmp(actual: str | None, op: str, expected: str) -> bool:
    if actual is None:
        return False
    if op == "equals":
        return actual == expected
    if op == "contains":
        return expected in actual
    return re.search(expected, actual) is not None


def _eval_count(elements: list[base.Element], a: CountMatch) -> AssertionResult:
    n = len(base.find_all(elements, a.sel.as_selector()))
    op, k = _count_op(a)
    ok = {"equals": n == k, "atLeast": n >= k, "atMost": n <= k}[op]
    reason = "" if ok else f"count {op}={k} を期待したが n={n}"
    return AssertionResult(ok, "count", f"count {op}={k}: {_sel_str(a.sel)}", reason)


def _count_op(a: CountMatch) -> tuple[str, int]:
    if a.equals is not None:
        return "equals", a.equals
    if a.at_least is not None:
        return "atLeast", a.at_least
    assert a.at_most is not None  # scenario 検証で 1 つ保証済み（§6.4）
    return "atMost", a.at_most


def _eval_state(elements: list[base.Element], kind: str, sel: Selector) -> AssertionResult:
    el, err = _resolve_one(elements, sel)
    detail = f"{kind}: {_sel_str(sel)}"
    if el is None:
        return AssertionResult(False, kind, detail, err)
    traits = el["traits"]
    if kind == "enabled":
        ok = base.Trait.NOT_ENABLED not in traits
    elif kind == "disabled":
        ok = base.Trait.NOT_ENABLED in traits
    else:  # selected
        ok = base.Trait.SELECTED in traits
    reason = "" if ok else f"{kind} を満たさない: traits={traits}"
    return AssertionResult(ok, kind, detail, reason)


def evaluate_one(elements: list[base.Element], a: Assertion) -> AssertionResult:
    """1 アサーションを評価（種別は scenario 検証で 1 つに保証済み。§6.4）。"""
    if a.exists is not None:
        return _eval_exists(elements, a.exists)
    if a.value is not None:
        return _eval_text(elements, "value", a.value)
    if a.label is not None:
        return _eval_text(elements, "label", a.label)
    if a.count is not None:
        return _eval_count(elements, a.count)
    if a.enabled is not None:
        return _eval_state(elements, "enabled", a.enabled)
    if a.disabled is not None:
        return _eval_state(elements, "disabled", a.disabled)
    if a.selected is not None:
        return _eval_state(elements, "selected", a.selected)
    raise AssertionError("空のアサーション（scenario 検証で弾かれるはず）")


def evaluate(elements: list[base.Element], assertions: list[Assertion]) -> list[AssertionResult]:
    """`expect` / `assert` 全件を評価する（AND は呼び出し側が `passed()` で判定）。"""
    return [evaluate_one(elements, a) for a in assertions]


def passed(results: list[AssertionResult]) -> bool:
    """全アサーションが ok なら True（AND。1 つでも失敗ならステップ失敗。§6.4）。"""
    return all(r.ok for r in results)
