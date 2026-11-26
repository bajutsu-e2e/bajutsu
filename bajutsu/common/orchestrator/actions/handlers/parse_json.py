"""The `parseJson` step: copy values out of JSON text into vars.*, by path (BE-0516).

No error message here quotes the text, a resolved value, or a substituted token: any of them may
derive from a secret. An entry is named by its position and its path as the author wrote it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from bajutsu.common.assertions.dict_match import json_text
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.orchestrator.actions.handlers.eval_ import MAX_CHARS
from bajutsu.common.orchestrator.types import DerivedSecrets
from bajutsu.common.scenario import ParseJson, ParseJsonField, Step
from bajutsu.common.scenario.interp import _TOKEN, find_tokens, interpolate
from bajutsu.common.scenario.models.actions.parse_json import first_duplicate_var

# One path atom: an unbracketed run of characters other than the three the grammar reserves for
# structure (`.` opens a key, `[`/`]` bracket an index) — BE-0440's fixed dot/index grammar, never
# a general expression language.
_KEY_CHARS = re.compile(r"[^.\[\]]+")
_INDEX_DIGITS = re.compile(r"[0-9]+")


class ParseJsonError(ValueError):
    """A `parseJson` step cannot be carried out; the message quotes no value."""


@dataclass(frozen=True)
class _KeySeg:
    name: str


@dataclass(frozen=True)
class _IndexSeg:
    n: int


_PathSeg = _KeySeg | _IndexSeg


def _substitute_path(path: str, where: str, bindings: Mapping[str, str]) -> str:
    """Substitute `${...}` tokens in a `path`, one path segment at a time (BE-0440).

    Unlike an ordinary field's whole-string splice, each substituted value is checked before it
    lands: one carrying `.`, `[`, or `]` would otherwise let a field name arriving from a response
    redirect the walk to a different field.
    """

    def repl(m: re.Match[str]) -> str:
        value = str(bindings[m.group(1).strip()])
        if not value or any(c in value for c in ".[]"):
            raise ParseJsonError(
                f"{where}: the value of {m.group(0)} must supply exactly one non-empty path "
                "segment (no '.', '[', or ']')"
            )
        return value

    return _TOKEN.sub(repl, path)


def _parse_path(path: str, where: str) -> list[_PathSeg]:
    """Parse a substituted `path` into `_KeySeg` / `_IndexSeg` segments.

    Raises:
        ParseJsonError: The path is empty, opens or continues with an unbracketed empty segment
            (including a trailing dot), or a bracketed index is not a non-negative decimal integer.
    """

    def malformed(reason: str) -> ParseJsonError:
        return ParseJsonError(f"{where} is malformed: {reason}")

    if not path:
        raise malformed("empty path")
    segments: list[_PathSeg] = []
    i, n = 0, len(path)
    while i < n:
        ch = path[i]
        if ch == "[":
            end = path.find("]", i)
            if end == -1:
                raise malformed("unterminated '['")
            digits = path[i + 1 : end]
            if not _INDEX_DIGITS.fullmatch(digits):
                raise malformed("an index is not a non-negative integer")
            # Compared as a length first: `int()` refuses a digit string past the interpreter's cap.
            if len(digits) > len(str(MAX_CHARS)):
                raise malformed("an index is out of range")
            segments.append(_IndexSeg(int(digits)))
            i = end + 1
            continue
        # The opening key carries no dot (`data.token`, never `.data.token`); every later key does.
        if i == 0:
            key_start = 0
        elif ch == ".":
            key_start = i + 1
        else:
            raise malformed(f"unexpected character at position {i}")
        m = _KEY_CHARS.match(path, key_start)
        if m is None:
            raise malformed("empty key segment")
        segments.append(_KeySeg(m.group(0)))
        i = m.end()
    return segments


def _resolve_path(value: object, segments: list[_PathSeg], where: str) -> object:
    """Walk parsed `segments` over a JSON value, failing on the first segment that can't resolve."""
    current = value
    for position, segment in enumerate(segments, start=1):
        at = f"{where}, segment {position}"
        if isinstance(segment, _KeySeg):
            if not isinstance(current, dict):
                raise ParseJsonError(f"{at}: a key is applied to a non-object value")
            if segment.name not in current:
                raise ParseJsonError(f"{at}: the key is missing")
            current = current[segment.name]
        else:
            if not isinstance(current, list):
                raise ParseJsonError(f"{at}: an index is applied to a non-array value")
            if segment.n >= len(current):
                raise ParseJsonError(f"{at}: the index is out of range")
            current = current[segment.n]
    return current


def target_names(spec: ParseJson, bindings: Mapping[str, str]) -> list[str]:
    """The `vars.*` names (without the prefix) the step writes, in entry order."""
    return [str(interpolate(field.var, bindings)) for field in spec.extract]


def _where(position: int, field: ParseJsonField) -> str:
    return f"extract entry {position} (path {field.path!r})"


def _stored_text(value: object, where: str) -> str:
    if isinstance(value, str):
        return value
    try:
        return json_text(value)
    except RecursionError:
        raise ParseJsonError(f"{where}: the value is nested too deeply") from None


def evaluate(spec: ParseJson, bindings: Mapping[str, str]) -> dict[str, str]:
    """The `vars.*` entries a `parseJson` step writes, keyed by binding name.

    Every entry resolves before anything is returned, so a caller writes all of them or none.

    Raises:
        ParseJsonError: A token is unbound, a substituted name or path segment fails its check,
            the text is over the input cap or not JSON, or a path is malformed or unresolved.
    """
    missing = sorted(find_tokens(spec.model_dump(by_alias=True)) - bindings.keys())
    if missing:
        # A token name is the author's own text, not a value, so naming it leaks nothing.
        raise ParseJsonError(f"no value is bound for {', '.join('${' + m + '}' for m in missing)}")
    names = target_names(spec, bindings)
    if not all(names):
        raise ParseJsonError("a var substituted to an empty name")
    if first_duplicate_var(names) is not None:
        raise ParseJsonError("two extract entries substitute to the same var")
    text = str(interpolate(spec.from_, bindings))
    if len(text) > MAX_CHARS:
        raise ParseJsonError(f"from: the string exceeds {MAX_CHARS} characters")
    paths: list[list[_PathSeg]] = []
    for position, field in enumerate(spec.extract, start=1):
        where = _where(position, field)
        paths.append(_parse_path(_substitute_path(field.path, where, bindings), where))
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        # Chained from None: the decoder's own error object holds the whole text.
        raise ParseJsonError("from: the text is not valid JSON") from None
    result: dict[str, str] = {}
    for position, (field, name, segments) in enumerate(
        zip(spec.extract, names, paths, strict=True), start=1
    ):
        where = _where(position, field)
        result[f"vars.{name}"] = _stored_text(_resolve_path(data, segments, where), where)
    return result


def record_parse_json(
    spec: ParseJson,
    names: list[str],
    bindings: Mapping[str, str],
    derived: DerivedSecrets,
    *,
    completed: bool,
) -> None:
    """After a `parseJson` step: update which variables are secret.

    *names* is `target_names` read before the step ran, since the step may overwrite a variable one
    of its own `var` tokens names. Fail-closed like `record_eval`: a value derived from a secret is
    masked whenever the variables hold one, even when a later check failed the step; only a
    *completed* step clears an earlier mark.
    """
    if any(f"vars.{n}" not in bindings for n in names):
        return
    tokens = find_tokens(spec.model_dump(by_alias=True))
    secret = any(
        t.startswith("secrets.")
        or (t.startswith("vars.") and t.removeprefix("vars.") in derived.secret_vars)
        for t in tokens
    )
    if secret:
        derived.register([bindings[f"vars.{n}"] for n in names])
        derived.secret_vars.update(names)
    elif completed:
        derived.secret_vars.difference_update(names)


@_handler("parse_json")
def _do_parse_json(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.parse_json is not None
    if bindings is None:
        return  # no var scope to write into (e.g. a bare condition eval) — nothing to do
    try:
        produced = evaluate(step.parse_json, bindings)
    except ParseJsonError as e:
        raise base.SelectorError(f"parseJson: {e}") from None
    bindings.update(produced)
