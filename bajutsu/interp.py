"""Interpolation of ${namespace.key} tokens over scenario data.

A single primitive shared by component expansion (params.*), data-driven runs (row.*),
and runtime variables / secrets (vars.* / secrets.*). The caller supplies a flat
`bindings` map whose keys are the full token names (e.g. "params.user", "row.q").

Tokens whose key is NOT in `bindings` are left untouched, so independent layers can each
substitute their own namespace without clobbering tokens meant for a later layer (e.g.
data-driven expansion substitutes row.* and leaves vars.* for the run loop).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_TOKEN = re.compile(r"\$\{([^}]+)\}")


def find_tokens(value: Any) -> set[str]:
    """Every token key referenced anywhere in a (possibly nested) value."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            found.update(m.group(1).strip() for m in _TOKEN.finditer(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return found


def _interp_str(s: str, bindings: Mapping[str, Any]) -> Any:
    # A string that is exactly one token returns the raw bound value (preserving its
    # type, e.g. a numeric row value); otherwise tokens are spliced into the string.
    whole = _TOKEN.fullmatch(s)
    if whole is not None and whole.group(1).strip() in bindings:
        return bindings[whole.group(1).strip()]

    def repl(mo: re.Match[str]) -> str:
        key = mo.group(1).strip()
        return str(bindings[key]) if key in bindings else mo.group(0)

    return _TOKEN.sub(repl, s)


def interpolate(value: Any, bindings: Mapping[str, Any]) -> Any:
    """Recursively replace ${key} for keys present in `bindings`; leave others intact."""
    if isinstance(value, str):
        return _interp_str(value, bindings)
    if isinstance(value, dict):
        return {k: interpolate(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, bindings) for v in value]
    return value
