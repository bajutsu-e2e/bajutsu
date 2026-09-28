"""The `http` step: issue an HTTP request (test-data setup, webhook triggers, API calls) and
optionally save the response body — whole (`saveBody`) or field-by-field (`extractBody`,
BE-0440) — to vars.*."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from bajutsu.common.assertions.evaluate import _json_text
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.scenario import HttpRequest, Step
from bajutsu.common.scenario.interp import _TOKEN, interpolate
from bajutsu.common.scenario.models.actions.http_request import first_duplicate_var

# One path atom: an unbracketed run of characters other than the three the grammar reserves for
# structure (`.` opens a key, `[`/`]` bracket an index) — BE-0440's fixed dot/index grammar, never
# a general expression language (see the item's own *Alternatives considered*).
_KEY_CHARS = re.compile(r"[^.\[\]]+")
_INDEX_DIGITS = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class _KeySeg:
    name: str


@dataclass(frozen=True)
class _IndexSeg:
    n: int


_PathSeg = _KeySeg | _IndexSeg


def _substitute_path(path: str, var: str, bindings: dict[str, str]) -> str:
    """Substitute `${...}` tokens in an `extractBody` `path`, one path segment at a time (BE-0440).

    Unlike an ordinary field's blind whole-string splice, each substituted value is checked before
    it lands: one carrying `.`, `[`, or `]` would otherwise let a field name arriving from a
    response body redirect the walk to a different field, so it fails the step instead. A token
    with no matching binding fails the same way, rather than the ordinary splice's silent
    leave-as-literal — this is the run's terminal substitution layer, so an unresolved reference is
    always an author error, never a token meant for a later layer to fill in.
    """

    def repl(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key not in bindings:
            raise base.SelectorError(
                f"http: extractBody var {var!r} path {path!r} — {m.group(0)} did not resolve "
                "to a value"
            )
        value = str(bindings[key])
        if not value or any(c in value for c in ".[]"):
            raise base.SelectorError(
                f"http: extractBody var {var!r} path {path!r} — substituted value {value!r} "
                "must supply exactly one non-empty path segment (no '.', '[', or ']')"
            )
        return value

    return _TOKEN.sub(repl, path)


def _substitute_var(var: str, bindings: dict[str, str]) -> str:
    """Ordinary whole-string `${...}` substitution for an `extractBody` `var` — a plain name with
    no path grammar of its own to protect, unlike `path` — except that an unresolved token still
    fails the step (BE-0440): `var` names the binding this step must populate, so silently writing
    it under the literal token text instead would leave that binding never set, with the step
    reporting success regardless.
    """
    substituted = str(interpolate(var, bindings))
    if "${" in substituted:
        raise base.SelectorError(
            f"http: extractBody var {var!r} did not fully resolve (still {substituted!r})"
        )
    return substituted


def _parse_path(path: str, var: str) -> list[_PathSeg]:
    """Parse a substituted `path` into `_KeySeg` / `_IndexSeg` segments.

    Raises:
        base.SelectorError: The path is empty, opens or continues with an unbracketed empty
            segment (including a trailing dot), or a bracketed index is not a non-negative
            decimal integer.
    """

    def malformed(reason: str) -> base.SelectorError:
        return base.SelectorError(
            f"http: extractBody var {var!r} path {path!r} is malformed: {reason}"
        )

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
                raise malformed(f"index {digits!r} is not a non-negative integer")
            segments.append(_IndexSeg(int(digits)))
            i = end + 1
            continue
        # The opening key carries no dot (`data.token`, never `.data.token`); every later key does.
        if i == 0:
            key_start = 0
        elif ch == ".":
            key_start = i + 1
        else:
            raise malformed(f"unexpected {ch!r} at position {i}")
        m = _KEY_CHARS.match(path, key_start)
        if m is None:
            raise malformed("empty key segment")
        segments.append(_KeySeg(m.group(0)))
        i = m.end()
    return segments


def _resolve_path(value: object, segments: list[_PathSeg], var: str, path: str) -> object:
    """Walk parsed `segments` over a JSON value, failing on the first segment that can't resolve."""
    current = value
    for segment in segments:
        if isinstance(segment, _KeySeg):
            if not isinstance(current, dict):
                raise base.SelectorError(
                    f"http: extractBody var {var!r} path {path!r} — key {segment.name!r} applied "
                    "to a non-object value"
                )
            if segment.name not in current:
                raise base.SelectorError(
                    f"http: extractBody var {var!r} path {path!r} — missing key {segment.name!r}"
                )
            current = current[segment.name]
        else:
            if not isinstance(current, list):
                raise base.SelectorError(
                    f"http: extractBody var {var!r} path {path!r} — index {segment.n} applied to "
                    "a non-array value"
                )
            if segment.n >= len(current):
                raise base.SelectorError(
                    f"http: extractBody var {var!r} path {path!r} — index {segment.n} out of "
                    f"range (length {len(current)})"
                )
            current = current[segment.n]
    return current


def _extract_body_fields(http: HttpRequest, body: str, bindings: dict[str, str] | None) -> None:
    """Resolve every `extractBody` entry against `body`, storing each as `vars.<var>` (BE-0440).

    All-or-nothing: every entry is parsed and resolved into a local list first, and `bindings` is
    written only once every entry has succeeded — never one entry's var left set while a later
    entry in the same list fails, which `after`/`interrupts` steps could otherwise read as if the
    whole step had populated cleanly.
    """
    if not http.extract_body or bindings is None:
        return
    substituted = [
        (_substitute_var(field.var, bindings), _substitute_path(field.path, field.var, bindings))
        for field in http.extract_body
    ]
    # Re-checked against the substituted names: a collision only `${vars.*}`/`${secrets.*}`
    # substitution reveals still fails the step, cleanly, here — rather than raising the uncaught
    # `ValidationError` a `Step`-level model validator would (BE-0440; see
    # `models/scenario/_http_extract.py` for why the load-time check alone can't cover this case).
    dup = first_duplicate_var((var for var, _ in substituted), http.save_body)
    if dup is not None:
        raise base.SelectorError(
            f"http: var {dup!r} is used more than once across extractBody/saveBody on the same step"
        )
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise base.SelectorError(f"http: extractBody — response body is not valid JSON: {e}") from e
    resolved_values = [
        (var, _resolve_path(parsed, _parse_path(path, var), var, path)) for var, path in substituted
    ]
    for var, resolved in resolved_values:
        bindings[f"vars.{var}"] = resolved if isinstance(resolved, str) else _json_text(resolved)


def _do_http(http: HttpRequest, bindings: dict[str, str] | None) -> None:
    """Execute an HTTP request and optionally save the response body to vars.*."""
    import urllib.error
    import urllib.request

    if not http.url.startswith(("http://", "https://")):
        raise base.SelectorError(f"http: only http/https URLs are allowed, got {http.url!r}")

    req = urllib.request.Request(  # noqa: S310 (scheme restricted to http/https above)
        http.url,
        data=http.body.encode("utf-8") if http.body else None,
        headers=dict(http.headers or {}),
        method=http.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (http/https only)
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code
    except urllib.error.URLError as e:
        raise base.SelectorError(f"http: request failed: {e.reason}") from e
    if http.status is not None and status != http.status:
        raise base.SelectorError(f"http: expected status {http.status}, got {status}")
    if http.save_body is not None and bindings is not None:
        bindings[f"vars.{http.save_body}"] = body
    _extract_body_fields(http, body, bindings)


@_handler("http")
def _do_http_action(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.http is not None
    _do_http(step.http, bindings)
