"""Assertion evaluation.

Evaluate a list of expect/assert against query() results (list[Element]). The
list is AND-ed; one failure fails the step. No AI is involved (machine checks
only).

Evaluation is total (returns results instead of raising) so it can be placed
straight into the report (manifest).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bajutsu.drivers import base
from bajutsu.network import NetworkExchange
from bajutsu.scenario import Assertion, CountMatch, Exists, RequestMatch, Selector, TextMatch


@dataclass(frozen=True)
class AssertionResult:
    ok: bool
    kind: str
    detail: str  # what was checked (for the report)
    reason: str = ""  # failure reason (empty when ok)


def _sel_str(sel: Selector) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in sel.as_selector().items())


def _resolve_one(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """Resolve a single element. On failure returns (None, reason).

    Ambiguous / not-found are treated as assertion failures.
    """
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
    assert a.matches is not None  # exactly one is guaranteed by scenario validation
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
    assert a.at_most is not None  # exactly one is guaranteed by scenario validation
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


def _match_request(ex: NetworkExchange, req: RequestMatch) -> bool:
    if req.method is not None and ex.method.upper() != req.method.upper():
        return False
    if req.url is not None and ex.url != req.url:
        return False
    if req.url_matches is not None and re.search(req.url_matches, ex.url) is None:
        return False
    if req.path is not None and ex.path != req.path:
        return False
    if req.path_matches is not None and re.search(req.path_matches, ex.path) is None:
        return False
    if req.status is not None and ex.status != req.status:
        return False
    return not (
        req.body_matches is not None
        and (ex.request_body is None or re.search(req.body_matches, ex.request_body) is None)
    )


def count_matching(exchanges: list[NetworkExchange], req: RequestMatch) -> int:
    """How many observed exchanges satisfy the request matcher (shared by the `request`
    assertion and the `until: { request }` wait)."""
    return sum(1 for ex in exchanges if _match_request(ex, req))


def request_label(req: RequestMatch) -> str:
    """Compact human description of a request matcher (e.g. "GET /items status=200")."""
    parts: list[str] = []
    if req.method is not None:
        parts.append(req.method.upper())
    if req.url is not None:
        parts.append(req.url)
    if req.url_matches is not None:
        parts.append(f"url~{req.url_matches}")
    if req.path is not None:
        parts.append(req.path)
    if req.path_matches is not None:
        parts.append(f"~{req.path_matches}")
    if req.status is not None:
        parts.append(f"status={req.status}")
    if req.body_matches is not None:
        parts.append(f"body~{req.body_matches}")
    if req.count is not None:
        parts.append(f"count={req.count}")
    return " ".join(parts)


def _eval_request(exchanges: list[NetworkExchange], req: RequestMatch) -> AssertionResult:
    n = count_matching(exchanges, req)
    ok = (n == req.count) if req.count is not None else (n >= 1)
    detail = f"request {request_label(req)}"
    if ok:
        return AssertionResult(True, "request", detail)
    reason = (
        f"count={req.count} を期待したが {n} 件"
        if req.count is not None
        else f"一致する通信なし（観測 {len(exchanges)} 件）"
    )
    return AssertionResult(False, "request", detail, reason)


def _assign_requests(exchanges: list[NetworkExchange], reqs: list[RequestMatch]) -> list[int]:
    """Assign each request matcher a *distinct* exchange — one `request` ↔ one exchange.

    Maximum bipartite matching (Kuhn's augmenting paths) so a broad matcher never steals
    the only exchange a more specific one needs. Returns, per matcher, the exchange index
    it was assigned, or -1 when none is left for it."""
    adj = [[j for j, ex in enumerate(exchanges) if _match_request(ex, req)] for req in reqs]
    ex_to_req = [-1] * len(exchanges)
    assigned = [-1] * len(reqs)

    def augment(i: int, seen: list[bool]) -> bool:
        for j in adj[i]:
            if not seen[j]:
                seen[j] = True
                if ex_to_req[j] == -1 or augment(ex_to_req[j], seen):
                    ex_to_req[j], assigned[i] = i, j
                    return True
        return False

    for i in range(len(reqs)):
        augment(i, [False] * len(exchanges))
    return assigned


def _request_assignment_result(
    req: RequestMatch, assigned_ex: int, exchanges: list[NetworkExchange]
) -> AssertionResult:
    detail = f"request {request_label(req)}"
    if assigned_ex != -1:
        return AssertionResult(True, "request", detail)
    matched_any = any(_match_request(ex, req) for ex in exchanges)
    reason = (
        "一致する通信は他の request と対応済み（request と通信は 1 対 1）"
        if matched_any
        else f"一致する通信なし（観測 {len(exchanges)} 件）"
    )
    return AssertionResult(False, "request", detail, reason)


def evaluate_one(
    elements: list[base.Element], a: Assertion, exchanges: list[NetworkExchange] | None = None
) -> AssertionResult:
    """Evaluate one assertion (the kind is guaranteed unique by scenario validation).

    UI kinds check `elements`; `request` checks the observed network `exchanges`."""
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
    if a.request is not None:
        return _eval_request(exchanges or [], a.request)
    raise AssertionError("空のアサーション（scenario 検証で弾かれるはず）")


def evaluate(
    elements: list[base.Element],
    assertions: list[Assertion],
    exchanges: list[NetworkExchange] | None = None,
) -> list[AssertionResult]:
    """Evaluate all of expect/assert (the caller decides AND via passed()).

    Plain `request` assertions (no `count`) in the block are matched **one-to-one** to
    distinct exchanges: two `request` lines need two separate exchanges. (`count` is an
    explicit aggregate and stays independent.)"""
    exs = exchanges or []
    bare: list[tuple[int, RequestMatch]] = []
    for i, a in enumerate(assertions):
        if a.request is not None and a.request.count is None:
            bare.append((i, a.request))
    assigned: dict[int, AssertionResult] = {}
    if len(bare) >= 2:
        order = _assign_requests(exs, [req for _, req in bare])
        for (i, req), ex_idx in zip(bare, order, strict=True):
            assigned[i] = _request_assignment_result(req, ex_idx, exs)
    return [
        assigned[i] if i in assigned else evaluate_one(elements, a, exs)
        for i, a in enumerate(assertions)
    ]


def passed(results: list[AssertionResult]) -> bool:
    """True iff every assertion is ok (AND; one failure fails the step)."""
    return all(r.ok for r in results)
