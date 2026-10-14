"""The load-time grammar of the `query` / `body` key/value dictionaries (BE-0463).

A matcher value is a scalar string, an operator map (`$matches` / `$exists`), a plain map (a subset
of a JSON object), or a list (an array matched element by element). Evaluation lives in
`bajutsu.common.assertions.dict_match`; this module only rejects a malformed matcher before the run.
"""

from __future__ import annotations

from typing import Any

from bajutsu.common.scenario.models.actions._functions import _check_regex

OPERATORS = frozenset({"$matches", "$exists"})


def is_operator_key(key: str) -> bool:
    """Whether a map key names an operator — a single `$` prefix; `$$` escapes a literal `$`."""
    return key.startswith("$") and not key.startswith("$$")


def unescape_key(key: str) -> str:
    """The JSON field / query parameter a plain key names (`$$ref` → `$ref`)."""
    return key[1:] if key.startswith("$$") else key


def is_operator_map(value: object) -> bool:
    """Whether a validated matcher value is an operator map (every key a single-`$` operator)."""
    return isinstance(value, dict) and bool(value) and all(is_operator_key(k) for k in value)


def _check_operators(ops: dict[str, Any], where: str) -> None:
    for op, arg in ops.items():
        if op not in OPERATORS:
            raise ValueError(f"{where}: unknown operator {op!r} (expected $matches or $exists)")
        if op == "$matches":
            if not isinstance(arg, str):
                raise ValueError(f"{where}.$matches must be a regex string")
            _check_regex(arg, f"{where}.$matches")
        elif not isinstance(arg, bool):
            raise ValueError(f"{where}.$exists must be true or false")


def _check_value(value: object, where: str, *, nested: bool) -> None:
    if isinstance(value, str):
        return
    if isinstance(value, dict):
        dollar = [k for k in value if is_operator_key(k)]
        if dollar and len(dollar) == len(value):
            _check_operators(value, where)
            return
        if dollar:
            raise ValueError(
                f"{where} mixes operator keys {dollar} with plain keys; write a literal `$` key as `$$`"
            )
        if not nested:
            raise ValueError(f"{where}: a query value is a string or an operator map, not a map")
        for key, sub in value.items():
            _check_value(sub, f"{where}.{key}", nested=True)
        return
    if isinstance(value, list):
        if not nested:
            raise ValueError(f"{where}: a query value is a string or an operator map, not a list")
        for i, sub in enumerate(value):
            _check_value(sub, f"{where}[{i}]", nested=True)
        return
    # An unquoted YAML number / boolean / null would compare against text and never match, so it
    # is refused rather than silently coerced (the `event.body` rule, BE-0048).
    raise ValueError(f'{where} must be a quoted string (write {value!r} as "{value}")')


def check_dict_matcher(matcher: dict[str, Any] | None, field: str) -> None:
    """Reject a malformed `query` / `body` dictionary at load time.

    Args:
        matcher: The dictionary as written, or None when the field is unset.
        field: `query` or `body` — `query` allows no nesting, `body` allows maps and lists.

    Raises:
        ValueError: The dictionary is empty (it would match every exchange), a top-level key is a
            bare operator, or a value breaks the grammar.
    """
    if matcher is None:
        return
    if not matcher:
        raise ValueError(f"{field} must not be empty: an empty dictionary matches every exchange")
    for key, value in matcher.items():
        if is_operator_key(key):
            raise ValueError(
                f"{field}.{key}: {field} keys name fields, not operators; write a literal `$` key as `$$`"
            )
        _check_value(value, f"{field}.{key}", nested=field == "body")
