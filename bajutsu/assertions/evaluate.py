"""Assertion dispatch and the small per-kind evaluators.

Evaluate a list of expect/assert against query() results (list[Element]). The list is AND-ed; one
failure fails the step. No AI is involved (machine checks only). Evaluation is total (returns
results instead of raising) so it can be placed straight into the report (manifest). The heavier
per-kind subsystems live in sibling modules: network matching in `network`, image preprocessing in
`visual`, JSON-Schema I/O in `schema`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bajutsu.assertions._common import (
    AssertionResult,
    _compile,
    _resolve_one,
    sel_str,
)
from bajutsu.assertions.network import (
    _assign_requests,
    _request_assignment_result,
    count_matching,
    match_request,
    request_label,
)
from bajutsu.assertions.schema import SchemaContext, _eval_response_schema
from bajutsu.assertions.visual import VisualContext, _eval_visual
from bajutsu.drivers import base
from bajutsu.evidence.network import NetworkExchange
from bajutsu.scenario import (
    ASSERTION_KINDS,
    Assertion,
    ClipboardMatch,
    CountMatch,
    CountOp,
    EventMatch,
    Exists,
    GoldenMatch,
    RequestMatch,
    Selector,
    TextMatch,
)


@dataclass(frozen=True)
class GoldenContext:
    """Paths a `golden` assertion needs (BE-0006).

    The golden JSON path (from the assertion's `path` field) is resolved against `goldens_dir`.
    `screen`, when given, is the authoritative device screen bounds for frame sanity checks;
    when absent, the bounds are derived from the live elements — a weaker fallback since
    elements at the screen edge make the check tautological for overflow detection.
    """

    goldens_dir: Path
    screen: base.Frame | None = None


@dataclass(frozen=True)
class EvalContext:
    """The per-run inputs the context-bearing assertion kinds need, bundled as one value (BE-0250).

    Each field feeds exactly one kind: `visual` the screenshot/baseline paths, `schema` the
    JSON-Schema directory, `golden` the goldens directory, and `clipboard` the device pasteboard
    text already read for the block. Bundling replaces the four loose keyword-only parameters that
    were threaded in lockstep through `evaluate` -> `evaluate_one` -> `run_scenario` ->
    `_run_step_body` -> the runner, so a new context-bearing kind adds a field here instead of a
    parameter at every layer. `clipboard` stays a resolved value here, not a reader: `evaluate`
    compares against whatever pasteboard text the caller resolved. The runner refreshes that value
    across a condition wait via `_clipboard_reader` (a `copy`'s pasteboard write can land a beat
    after the actuator returns), but that re-reading lives in the runner's poll, not in `evaluate`.
    """

    visual: VisualContext | None = None
    schema: SchemaContext | None = None
    golden: GoldenContext | None = None
    clipboard: str | None = None


def _eval_exists(elements: list[base.Element], a: Exists) -> AssertionResult:
    found = len(base.find_all(elements, a.sel.as_selector())) >= 1
    ok = found != a.negate
    want = "absent" if a.negate else "present"
    reason = "" if ok else f"expected {want} but was {'present' if found else 'absent'}"
    return AssertionResult(ok, "exists", f"{want}: {sel_str(a.sel)}", reason)


def _eval_text(elements: list[base.Element], kind: str, a: TextMatch) -> AssertionResult:
    el, err = _resolve_one(elements, a.sel)
    detail_base = f"{kind}: {sel_str(a.sel)}"
    if el is None:
        return AssertionResult(False, kind, detail_base, err)
    actual = el["value"] if kind == "value" else el["label"]
    op, expected = _text_op(a)
    ok = _text_cmp(actual, op, expected)
    reason = "" if ok else f"expected {op}={expected!r} but actual={actual!r}"
    return AssertionResult(ok, kind, f"{kind} {op}={expected!r}: {sel_str(a.sel)}", reason)


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
    return _compile(expected).search(actual) is not None


def _eval_count(elements: list[base.Element], a: CountMatch) -> AssertionResult:
    n = len(base.find_all(elements, a.sel.as_selector()))
    op, k = _count_op(a)
    ok = {"equals": n == k, "atLeast": n >= k, "atMost": n <= k}[op]
    reason = "" if ok else f"expected count {op}={k} but n={n}"
    return AssertionResult(ok, "count", f"count {op}={k}: {sel_str(a.sel)}", reason)


def _count_op(a: CountMatch) -> tuple[str, int]:
    if a.equals is not None:
        return "equals", a.equals
    if a.at_least is not None:
        return "atLeast", a.at_least
    assert a.at_most is not None  # exactly one is guaranteed by scenario validation
    return "atMost", a.at_most


def _eval_state(elements: list[base.Element], kind: str, sel: Selector) -> AssertionResult:
    el, err = _resolve_one(elements, sel)
    detail = f"{kind}: {sel_str(sel)}"
    if el is None:
        return AssertionResult(False, kind, detail, err)
    traits = el["traits"]
    if kind == "enabled":
        ok = base.Trait.NOT_ENABLED not in traits
    elif kind == "disabled":
        ok = base.Trait.NOT_ENABLED in traits
    else:  # selected
        ok = base.Trait.SELECTED in traits
    reason = "" if ok else f"not {kind}: traits={traits}"
    return AssertionResult(ok, kind, detail, reason)


def _eval_request(exchanges: list[NetworkExchange], req: RequestMatch) -> AssertionResult:
    n = count_matching(exchanges, req)
    ok = (n == req.count) if req.count is not None else (n >= 1)
    detail = f"request {request_label(req)}"
    if ok:
        return AssertionResult(True, "request", detail)
    reason = (
        f"expected count={req.count} but matched {n}"
        if req.count is not None
        else f"no matching exchange (observed {len(exchanges)})"
    )
    return AssertionResult(False, "request", detail, reason)


def _count_op_label(c: CountOp) -> str:
    if c.equals is not None:
        return f"=={c.equals}"
    if c.at_least is not None:
        return f">={c.at_least}"
    return f"<={c.at_most}"


def _count_satisfied(n: int, c: CountOp | None) -> bool:
    """Whether `n` matches satisfy the count operator (default: at least one)."""
    if c is None:
        return n >= 1
    if c.equals is not None:
        return n == c.equals
    if c.at_least is not None:
        return n >= c.at_least
    return c.at_most is not None and n <= c.at_most


def _json_text(value: object) -> str:
    """Canonical text form of a JSON value for comparing an event body field.

    Booleans and null render JSON-style (`true` / `false` / `null`), and a nested array / object
    renders as compact JSON, so a YAML matcher reads the way the captured body does, not as a Python
    `repr` (`True` / `None` / single-quoted dicts). Numbers / strings keep their plain form.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _event_body_matches(ex: NetworkExchange, body: dict[str, str]) -> bool:
    """Whether the exchange's JSON request body carries every given field, each equal (as text).

    A non-JSON / non-object / absent body matches no body criterion.
    """
    if not body:
        return True
    if ex.request_body is None:
        return False
    try:
        parsed = json.loads(ex.request_body)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return all(key in parsed and _json_text(parsed[key]) == want for key, want in body.items())


def _event_label(m: EventMatch) -> str:
    """Compact human description of an event matcher (endpoint + body + count)."""
    parts: list[str] = []
    if m.method is not None:
        parts.append(m.method.upper())
    if m.url is not None:
        parts.append(m.url)
    if m.url_matches is not None:
        parts.append(f"url~{m.url_matches}")
    if m.path is not None:
        parts.append(m.path)
    if m.path_matches is not None:
        parts.append(f"~{m.path_matches}")
    if m.body:
        parts.append(f"body={m.body}")
    if m.count is not None:
        parts.append(f"count{_count_op_label(m.count)}")
    return " ".join(parts)


def _eval_event(exchanges: list[NetworkExchange], m: EventMatch) -> AssertionResult:
    """Assert an analytics/telemetry event the app sent (BE-0048).

    Filter the timeline by the event's endpoint (reusing the request matcher), then by its
    structured request-body fields, and check the surviving count against the operator. Pure over
    the captured exchanges.
    """
    endpoint = (m.method, m.url, m.url_matches, m.path, m.path_matches)
    if any(v is not None for v in endpoint):
        req = RequestMatch(
            method=m.method,
            url=m.url,
            urlMatches=m.url_matches,
            path=m.path,
            pathMatches=m.path_matches,
        )
        candidates = [ex for ex in exchanges if match_request(ex, req)]
    else:
        candidates = exchanges
    n = sum(1 for ex in candidates if _event_body_matches(ex, m.body))
    detail = f"event {_event_label(m)}"
    if _count_satisfied(n, m.count):
        return AssertionResult(True, "event", detail)
    want = f"count{_count_op_label(m.count)}" if m.count is not None else "at least one"
    return AssertionResult(
        False,
        "event",
        detail,
        f"expected {want}, matched {n} (observed {len(exchanges)} exchanges)",
    )


def _eval_request_sequence(
    exchanges: list[NetworkExchange], seq: list[RequestMatch]
) -> AssertionResult:
    """Assert a set of request matchers were observed in order (BE-0048).

    Each matches a distinct exchange at a strictly later position than the previous, so unrelated
    traffic may interleave. A greedy forward scan is optimal for this order-preserving subsequence.
    Pure over the timeline.
    """
    detail = "requestSequence " + " → ".join(request_label(r, with_count=False) for r in seq)
    i = 0
    for pos, req in enumerate(seq):
        while i < len(exchanges) and not match_request(exchanges[i], req):
            i += 1
        if i >= len(exchanges):
            reason = (
                f"step {pos} ({request_label(req, with_count=False)}) not observed in order "
                f"(matched {pos} of {len(seq)} so far; observed {len(exchanges)} exchanges)"
            )
            return AssertionResult(False, "requestSequence", detail, reason)
        i += 1
    return AssertionResult(True, "requestSequence", detail)


def _eval_clipboard(clipboard: str | None, m: ClipboardMatch) -> AssertionResult:
    op = "equals" if m.equals is not None else "matches"
    expected = m.equals if m.equals is not None else m.matches
    detail = f"clipboard {op}={expected!r}"
    if clipboard is None:
        # No device-control channel read the pasteboard (fake driver / parallel run, or the read
        # failed). A clean not-ok, like the visual assertion with no context — never a crash.
        return AssertionResult(
            False, "clipboard", detail, "no clipboard read (device control unavailable)"
        )
    if m.equals is not None:
        ok = clipboard == m.equals
    else:
        assert m.matches is not None
        ok = re.search(m.matches, clipboard) is not None
    reason = "" if ok else f"clipboard was {clipboard!r}, expected {op}={expected!r}"
    return AssertionResult(ok, "clipboard", detail, reason)


def _eval_golden(
    elements: list[base.Element], m: GoldenMatch, ctx: GoldenContext | None
) -> AssertionResult:
    detail = f"golden ≈ {m.path}"
    if ctx is None:
        return AssertionResult(False, "golden", detail, "no golden context provided")
    goldens_dir = ctx.goldens_dir.resolve()
    golden_file = (goldens_dir / m.path).resolve()
    if not golden_file.is_relative_to(goldens_dir):
        return AssertionResult(
            False, "golden", detail, f"golden path escapes the goldens dir: {m.path}"
        )
    if not golden_file.is_file():
        return AssertionResult(False, "golden", detail, f"golden not found: {m.path}")
    from bajutsu.evidence.golden import compare_golden, load_golden

    golden = load_golden(golden_file)
    if ctx.screen is not None:
        screen = ctx.screen
    else:
        from bajutsu.elements import screen_size_from_elements

        sw, sh = screen_size_from_elements(elements)
        screen = (0.0, 0.0, sw, sh)
    result = compare_golden(golden, elements, screen)
    if result.ok:
        return AssertionResult(True, "golden", detail)
    parts: list[str] = []
    if result.mismatches:
        parts.append("; ".join(str(mm) for mm in result.mismatches))
    if result.missing:
        parts.append(f"missing: {', '.join(result.missing)}")
    if result.frame_failures:
        parts.append(f"frame failures: {', '.join(result.frame_failures)}")
    return AssertionResult(False, "golden", detail, "; ".join(parts))


# Assertion evaluators keyed by kind (the `Assertion` field name), each a thin adapter over the
# per-kind `_eval_*` above that pulls the one set field off the assertion and the inputs its kind
# needs. Dispatch is a lookup on the set field, replacing the 14-way `if a.X is not None` chain
# (BE-0250) — the same self-registering pattern `orchestrator/actions/_registry.py` uses for step
# actions. The uniform signature is why each adapter asserts its own field is set (the caller only
# reaches it when it is): it lets one dict hold every kind under strict typing, as `_HANDLERS` does.
_Evaluator = Callable[
    [Assertion, list[base.Element], list[NetworkExchange], EvalContext],
    AssertionResult,
]
_EVALUATORS: dict[str, _Evaluator] = {}


def _evaluator(kind: str) -> Callable[[_Evaluator], _Evaluator]:
    def register(fn: _Evaluator) -> _Evaluator:
        _EVALUATORS[kind] = fn
        return fn

    return register


@_evaluator("exists")
def _do_exists(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.exists is not None
    return _eval_exists(elements, a.exists)


@_evaluator("value")
def _do_value(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.value is not None
    return _eval_text(elements, "value", a.value)


@_evaluator("label")
def _do_label(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.label is not None
    return _eval_text(elements, "label", a.label)


@_evaluator("count")
def _do_count(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.count is not None
    return _eval_count(elements, a.count)


@_evaluator("enabled")
def _do_enabled(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.enabled is not None
    return _eval_state(elements, "enabled", a.enabled)


@_evaluator("disabled")
def _do_disabled(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.disabled is not None
    return _eval_state(elements, "disabled", a.disabled)


@_evaluator("selected")
def _do_selected(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.selected is not None
    return _eval_state(elements, "selected", a.selected)


@_evaluator("request")
def _do_request(
    a: Assertion, _el: list[base.Element], exchanges: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.request is not None
    return _eval_request(exchanges, a.request)


@_evaluator("event")
def _do_event(
    a: Assertion, _el: list[base.Element], exchanges: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.event is not None
    return _eval_event(exchanges, a.event)


@_evaluator("request_sequence")
def _do_request_sequence(
    a: Assertion, _el: list[base.Element], exchanges: list[NetworkExchange], _c: EvalContext
) -> AssertionResult:
    assert a.request_sequence is not None
    return _eval_request_sequence(exchanges, a.request_sequence)


@_evaluator("response_schema")
def _do_response_schema(
    a: Assertion, _el: list[base.Element], exchanges: list[NetworkExchange], ctx: EvalContext
) -> AssertionResult:
    assert a.response_schema is not None
    return _eval_response_schema(exchanges, a.response_schema, ctx.schema)


@_evaluator("visual")
def _do_visual(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], ctx: EvalContext
) -> AssertionResult:
    assert a.visual is not None
    return _eval_visual(ctx.visual, a.visual, elements)


@_evaluator("clipboard")
def _do_clipboard(
    a: Assertion, _el: list[base.Element], _e: list[NetworkExchange], ctx: EvalContext
) -> AssertionResult:
    assert a.clipboard is not None
    return _eval_clipboard(ctx.clipboard, a.clipboard)


@_evaluator("golden")
def _do_golden(
    a: Assertion, elements: list[base.Element], _e: list[NetworkExchange], ctx: EvalContext
) -> AssertionResult:
    assert a.golden is not None
    return _eval_golden(elements, a.golden, ctx.golden)


def evaluate_one(
    elements: list[base.Element],
    a: Assertion,
    exchanges: list[NetworkExchange] | None = None,
    *,
    ctx: EvalContext | None = None,
) -> AssertionResult:
    """Evaluate one assertion against the screen and the observed network.

    The assertion's kind is guaranteed unique by scenario validation, so exactly one kind is set;
    dispatch is a `_EVALUATORS` lookup on that set field. Evaluation is total — a *failed* check
    returns a not-ok result rather than raising.

    Args:
        elements: One `query()` snapshot; the UI kinds (`exists` / `value` / `label` / `count` /
            `enabled` / `disabled` / `selected`) check it.
        a: The assertion to evaluate.
        exchanges: The network exchanges observed so far; the `request` / `event` /
            `requestSequence` kinds check these (None is treated as empty).
        ctx: The per-kind inputs (`visual` / `schema` / `golden` / `clipboard`); a kind whose input
            is absent fails cleanly rather than raising. None is an empty context (BE-0250).

    Returns:
        The single assertion's result.

    Raises:
        AssertionError: The assertion has no kind set — scenario validation should have caught this.
    """
    if ctx is None:
        ctx = EvalContext()
    exs = exchanges or []
    for kind in ASSERTION_KINDS:
        if getattr(a, kind) is not None:
            return _EVALUATORS[kind](a, elements, exs, ctx)
    raise AssertionError("empty assertion (should be caught by scenario validation)")


def evaluate(
    elements: list[base.Element],
    assertions: list[Assertion],
    exchanges: list[NetworkExchange] | None = None,
    *,
    ctx: EvalContext | None = None,
) -> list[AssertionResult]:
    """Evaluate every assertion in an expect/assert block (the caller AND-s them via `passed`).

    Plain `request` assertions (no `count`) in the block are matched **one-to-one** to distinct
    exchanges: two `request` lines need two separate exchanges. `count` is an explicit aggregate and
    stays independent of that one-to-one assignment.

    Args:
        elements: One `query()` snapshot for the UI kinds.
        assertions: The block's assertions, evaluated in order.
        exchanges: The network exchanges observed so far (None is treated as empty).
        ctx: The per-kind inputs forwarded to each `evaluate_one` (see there); None is an empty
            context (BE-0250).

    Returns:
        One result per assertion, positionally aligned with `assertions`.
    """
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
        assigned[i] if i in assigned else evaluate_one(elements, a, exs, ctx=ctx)
        for i, a in enumerate(assertions)
    ]


def passed(results: list[AssertionResult]) -> bool:
    """True iff every assertion is ok (AND; one failure fails the step)."""
    return all(r.ok for r in results)
