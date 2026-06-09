"""Tests for the ${ns.key} interpolation primitive."""

from __future__ import annotations

from bajutsu.interp import find_tokens, interpolate


def test_find_tokens_nested() -> None:
    value = {"a": "${row.q}", "b": ["${params.user}", "lit"], "c": {"d": "x${vars.n}y"}}
    assert find_tokens(value) == {"row.q", "params.user", "vars.n"}


def test_whole_string_token_returns_raw_value() -> None:
    # A single-token string yields the raw bound value (type preserved).
    assert interpolate("${row.n}", {"row.n": 3}) == 3
    assert interpolate("${params.user}", {"params.user": "alice"}) == "alice"


def test_token_spliced_into_string() -> None:
    assert interpolate("hi ${params.user}!", {"params.user": "alice"}) == "hi alice!"
    assert interpolate("${row.n} items", {"row.n": 3}) == "3 items"


def test_unknown_tokens_left_intact() -> None:
    # row.* substituted now; vars.* deliberately left for a later layer.
    assert interpolate("${row.q}/${vars.x}", {"row.q": "dog"}) == "dog/${vars.x}"


def test_nested_structure() -> None:
    out = interpolate(
        {"type": {"into": {"id": "f"}, "text": "${params.q}"}, "tags": ["${row.t}"]},
        {"params.q": "cat", "row.t": "smoke"},
    )
    assert out == {"type": {"into": {"id": "f"}, "text": "cat"}, "tags": ["smoke"]}


def test_non_string_values_pass_through() -> None:
    assert interpolate(5, {"x": 1}) == 5
    assert interpolate(True, {}) is True
    assert interpolate(None, {}) is None
