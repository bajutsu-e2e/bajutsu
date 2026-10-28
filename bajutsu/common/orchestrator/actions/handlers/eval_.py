"""The `eval` step: transform a value through a fixed operation vocabulary into vars.* (BE-0472)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal

from bajutsu.common.assertions.dict_match import json_text
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.orchestrator.types import DerivedSecrets
from bajutsu.common.scenario import Eval, Step
from bajutsu.common.scenario.interp import find_tokens, interpolate
from bajutsu.common.scenario.models.actions.eval_ops import (
    Bare,
    Case,
    EvalOp,
    EvalOpError,
    Filter,
    Join,
    Pick,
    RegexOp,
    Replace,
    Sort,
    Split,
    Substring,
    parse_ops,
)

# Every value a pipeline holds is bounded, so a `replace` or `join` cannot grow one without end and
# a run's report stays a size a reader can open. BE-0472's open points list both as starting points.
MAX_CHARS = 1_048_576
MAX_ITEMS = 10_000

_DECIMAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")

Value = str | list[str]


def _checked(value: Value, where: str) -> Value:
    if isinstance(value, str):
        if len(value) > MAX_CHARS:
            raise EvalOpError(f"{where}: the string exceeds {MAX_CHARS} characters")
    elif len(value) > MAX_ITEMS:
        raise EvalOpError(f"{where}: the list exceeds {MAX_ITEMS} elements")
    return value


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error:
        raise EvalOpError(f"{where}: not a valid regular expression") from None


def _split(text: str, op: Split, where: str) -> list[str]:
    if not op.regex:
        return text.split(op.by)
    # Slicing between matches rather than `re.split`, which would splice capture groups (and a None
    # for one that did not participate) into the result.
    out, last = [], 0
    for m in _compile(op.by, where).finditer(text):
        out.append(text[last : m.start()])
        last = m.end()
    out.append(text[last:])
    return out


def _keep(item: str, op: Filter, pattern: re.Pattern[str] | None) -> bool:
    if op.kind == "notEmpty":
        hit = item != ""
    elif op.kind == "equals":
        hit = item == op.arg
    elif op.kind == "contains":
        hit = op.arg in item
    else:
        assert pattern is not None
        hit = pattern.search(item) is not None
    return hit != op.negate


def _number(item: str, where: str) -> Decimal:
    if not _DECIMAL.fullmatch(item):
        raise EvalOpError(f"{where}: an element is not a decimal number")
    return Decimal(item)


def _sort(items: list[str], op: Sort, where: str) -> list[str]:
    ordered = sorted(items, key=lambda i: _number(i, where)) if op.numeric else sorted(items)
    # `desc` is defined as `sort` followed by `reverse`, so ties come out reversed too.
    return ordered[::-1] if op.order == "desc" else ordered


def _pick(items: list[str], op: Pick, where: str) -> str:
    index = 0 if op.at == "first" else -1 if op.at == "last" else op.at
    if not items or index >= len(items):
        raise EvalOpError(f"{where}: the index is outside the list")
    return items[index]


def _parse_json(text: str, where: str) -> list[str]:
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise EvalOpError(f"{where}: the text is not a JSON array") from None
    if not isinstance(data, list):
        raise EvalOpError(f"{where}: the text is not a JSON array")
    return [e if isinstance(e, str) else json_text(e) for e in data]


def _regex(text: str, op: RegexOp, where: str) -> str:
    pattern = _compile(op.pattern, where)
    if op.group > pattern.groups:
        raise EvalOpError(f"{where}: the pattern has no group {op.group}")
    m = pattern.search(text)
    if m is None or m.group(op.group) is None:
        raise EvalOpError(f"{where}: the pattern does not match")
    return m.group(op.group)


def _replace(text: str, op: Replace, where: str) -> str:
    if not op.regex:
        return text.replace(op.find, op.with_)
    try:
        return _compile(op.find, where).sub(op.with_, text)
    except (re.error, IndexError):
        raise EvalOpError(f"{where}: the replacement is not valid for the pattern") from None


def _substring(text: str, op: Substring, where: str) -> str:
    end = len(text) if op.end is None else op.end
    if op.start > len(text) or end > len(text) or op.start > end:
        raise EvalOpError(f"{where}: start or end is outside the string")
    return text[op.start : end]


def _bare(value: Value, op: Bare) -> Value:
    if op.name == "trim":
        assert isinstance(value, str)
        return value.strip()
    assert isinstance(
        value, list
    )  # `json`, the other string-taking bare op, is handled by `_apply`
    if op.name == "count":
        return str(len(value))
    if op.name == "unique":
        return list(dict.fromkeys(value))
    return value[::-1]


def _apply(value: Value, op: EvalOp, where: str) -> Value:  # one branch per operation
    # The load-time type flow already proved each operation receives its kind; the asserts restate
    # that for mypy rather than re-check it.
    if isinstance(op, Split):
        assert isinstance(value, str)
        return _split(value, op, where)
    if isinstance(op, Join):
        assert isinstance(value, list)
        return op.by.join(value)
    if isinstance(op, Filter):
        assert isinstance(value, list)
        pattern = _compile(op.arg, where) if op.kind == "matches" else None
        return [i for i in value if _keep(i, op, pattern)]
    if isinstance(op, Pick):
        assert isinstance(value, list)
        return _pick(value, op, where)
    if isinstance(op, Sort):
        assert isinstance(value, list)
        return _sort(value, op, where)
    if isinstance(op, RegexOp):
        assert isinstance(value, str)
        return _regex(value, op, where)
    if isinstance(op, Replace):
        assert isinstance(value, str)
        return _replace(value, op, where)
    if isinstance(op, Substring):
        assert isinstance(value, str)
        return _substring(value, op, where)
    if isinstance(op, Case):
        assert isinstance(value, str)
        return value.lower() if op.to == "lower" else value.upper()
    if op.name == "json":
        assert isinstance(value, str)
        return _parse_json(value, where)
    return _bare(value, op)


def run_ops(source: str, ops: Sequence[EvalOp]) -> Value:
    """Run a parsed pipeline over *source*, every intermediate value held under the caps.

    Raises:
        EvalOpError: an operation fails on this value, or a value exceeds a cap. The message names
            the operation's position and never quotes a value, which may derive from a secret.
    """
    value: Value = _checked(source, "from")
    for position, op in enumerate(ops, start=1):
        where = f"op {position} ({_name(op)})"
        value = _checked(_apply(value, op, where), where)
    return value


def _name(op: EvalOp) -> str:
    if isinstance(op, Bare):
        return op.name
    if isinstance(op, RegexOp):
        return "regex"
    return type(op).__name__.lower()


def stored_text(value: Value) -> str:
    """The text `${vars.*}` carries for a pipeline result: a list as compact JSON array text."""
    return value if isinstance(value, str) else json_text(value)


def _substituted(spec: Eval, bindings: Mapping[str, str]) -> tuple[str, list[object]]:
    """`from` and `ops` with every `${...}` token resolved, refusing one the run has no value for."""
    raw = {"from": spec.from_, "ops": spec.ops}
    missing = sorted(find_tokens(raw) - bindings.keys())
    if missing:
        # A token name is the author's own text, not a value, so naming it leaks nothing.
        raise EvalOpError(f"no value is bound for {', '.join('${' + m + '}' for m in missing)}")
    done = interpolate(raw, bindings)
    return str(done["from"]), list(done["ops"])


def evaluate(spec: Eval, bindings: Mapping[str, str]) -> dict[str, str]:
    """The `vars.*` entries an `eval` step writes, keyed by binding name.

    Raises:
        EvalOpError: a token is unbound, a substituted argument fails its check, an operation
            fails, or `into.vars` names more variables than the list holds.
    """
    source, raw_ops = _substituted(spec, bindings)
    result = run_ops(source, parse_ops(raw_ops))
    if spec.into.var is not None:
        return {f"vars.{spec.into.var}": stored_text(result)}
    assert isinstance(result, list)  # the loader allows `into.vars` only on a list result
    names = spec.into.names
    if len(result) < len(names):
        raise EvalOpError(f"into.vars names {len(names)} variables, but the list is shorter")
    return {f"vars.{n}": item for n, item in zip(names, result, strict=False)}


def _op_name(raw: object) -> str:
    return raw if isinstance(raw, str) else next(iter(raw)) if isinstance(raw, Mapping) else ""


def _derives_from_secret(spec: Eval, secret_vars: set[str]) -> bool:
    """Whether an `eval` result carries a character of a secret input, read from the written step.

    `count` turns its input into an element count, which carries no character of it, so only `from`
    and the arguments *after* the last `count` can pass a secret on. Without that exception a count
    over a secret-derived list would mask a bare digit throughout every later artifact.
    """
    names = [_op_name(op) for op in spec.ops]
    last_count = max((i for i, n in enumerate(names) if n == "count"), default=None)
    inputs: list[object] = (
        [spec.from_, *spec.ops] if last_count is None else list(spec.ops[last_count + 1 :])
    )
    for token in find_tokens(inputs):
        if token.startswith("secrets."):
            return True
        if token.startswith("vars.") and token.removeprefix("vars.") in secret_vars:
            return True
    return False


def record_eval(spec: Eval, bindings: Mapping[str, str], derived: DerivedSecrets) -> str:
    """After a successful `eval`: update which variables are secret, and return the produced text.

    A result derived from a secret joins the values every later artifact of this scenario masks, and
    its variables are marked secret; any other result clears the mark, since the variable now holds
    a different value.

    Returns:
        The value the step produced, for the step outcome — a list spread over `into.vars` as the
        JSON array of the elements it assigned.
    """
    names = spec.into.names
    values = [bindings[f"vars.{n}"] for n in names]
    if _derives_from_secret(spec, derived.secret_vars):
        derived.register(values)
        derived.secret_vars.update(names)
    else:
        derived.secret_vars.difference_update(names)
    return values[0] if spec.into.var is not None else json_text(values)


@_handler("eval_")
def _do_eval(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.eval_ is not None
    if bindings is None:
        return  # no var scope to write into (e.g. a bare condition eval) — nothing to do
    try:
        produced = evaluate(step.eval_, bindings)
    except EvalOpError as e:
        # A SelectorError is the step failure the run loop reports; nothing is written, so a failed
        # pipeline leaves every target variable as it was.
        raise base.SelectorError(f"eval: {e}") from None
    bindings.update(produced)
