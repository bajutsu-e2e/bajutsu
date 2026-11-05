"""Key/value dictionary matching over a request's query string and JSON body (BE-0463).

One pure evaluator serves every network matcher that carries `query` / `body` — `request`,
`event`, `requestSequence`, `responseSchema.request`, and `until: {request}` — so they all read a
dictionary the same way. It returns the first mismatch as text rather than a bare bool, so a
failing assertion can name the key instead of only a count.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from bajutsu.common.assertions._common import _compile
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario.models.assertions._dict_matcher import is_operator_map, unescape_key

_SHOWN_CHARS = (
    80  # an observed value is cut here in a failure message, so a large blob stays legible
)


def json_text(value: object) -> str:
    """Canonical text form of a JSON value, the form every scalar and `$matches` compares against.

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


def _shown(text: str) -> str:
    quoted = json.dumps(text, ensure_ascii=False)
    return quoted if len(quoted) <= _SHOWN_CHARS else quoted[: _SHOWN_CHARS - 1] + "…"


def _got(observed: list[str]) -> str:
    """How a failure message shows a key's observed values — one, several, or none."""
    if not observed:
        return "nothing"
    if len(observed) == 1:
        return _shown(observed[0])
    return "[" + ", ".join(_shown(o) for o in observed) + "]"


def _operators_mismatch(
    ops: dict[str, Any], present: bool, observed: Callable[[], list[str]], where: str
) -> str | None:
    """Check an operator map against one key's observed text forms.

    `observed` is called only when an operator needs the text, so an `$exists`-only check on a
    large nested value never serializes it — this runs on every `until: {request}` poll.
    """
    exists = ops.get("$exists")
    if exists is True and not present:
        return f"{where}: expected present, got nothing"
    if exists is False and present:
        return f"{where}: expected absent, got {_got(observed())}"
    pattern = ops.get("$matches")
    if pattern is None:
        return None
    texts = observed() if present else []
    if any(_compile(pattern).search(t) for t in texts):
        return None
    return f"{where}: expected to match /{pattern}/, got {_got(texts)}"


def _body_mismatch(want: object, present: bool, got: object, where: str) -> str | None:
    if isinstance(want, dict) and is_operator_map(want):
        return _operators_mismatch(want, present, lambda: [json_text(got)], where)
    if not present:
        return (
            f"{where}: expected {_shown(want) if isinstance(want, str) else 'a value'}, got nothing"
        )
    if isinstance(want, str):
        text = json_text(got)
        return None if text == want else f"{where}: expected {_shown(want)}, got {_shown(text)}"
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return f"{where}: expected an object, got {_shown(json_text(got))}"
        for key, sub in want.items():
            name = unescape_key(key)
            miss = _body_mismatch(sub, name in got, got.get(name), f"{where}.{name}")
            if miss is not None:
                return miss
        return None
    if not isinstance(want, list):
        # Unreachable past load-time validation; refuse loudly rather than call a value a mismatch.
        raise TypeError(f"{where}: not a dictionary matcher value: {want!r}")
    if not isinstance(got, list) or len(got) != len(want):
        return f"{where}: expected an array of {len(want)} elements, got {_shown(json_text(got))}"
    for i, (sub, item) in enumerate(zip(want, got, strict=True)):
        miss = _body_mismatch(sub, True, item, f"{where}[{i}]")
        if miss is not None:
            return miss
    return None


def dict_mismatch(
    ex: NetworkExchange, query: dict[str, Any] | None, body: dict[str, Any] | None
) -> str | None:
    """The first `query` / `body` key the exchange fails, described for a failure message.

    Args:
        ex: One observed network exchange.
        query: The validated `query` dictionary, or None when unset.
        body: The validated `body` dictionary, or None when unset.

    Returns:
        None when every key holds; otherwise one line such as
        ``query.page: expected "2", got "3"``.
    """
    if query is not None:
        params = ex.query_params
        for key, want in query.items():
            name = unescape_key(key)
            observed = params.get(name, [])
            where = f"query.{name}"
            # A repeated key holds when any occurrence satisfies the value.
            if isinstance(want, str):
                if want not in observed:
                    return f"{where}: expected {_shown(want)}, got {_got(observed)}"
                continue
            miss = _operators_mismatch(want, bool(observed), observed.copy, where)
            if miss is not None:
                return miss
    if body is not None:
        parsed = ex.request_json_object
        if parsed is None:
            if ex.request_body is None:
                return "body: request has no body"
            return "body: request body is not a JSON object"
        return _body_mismatch(body, True, parsed, "body")
    return None
