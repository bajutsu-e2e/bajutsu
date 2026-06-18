"""Tests for the orchestrator if/forEach control-flow steps (deterministic branch + loop)."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


def test_if_takes_then_branch() -> None:
    driver = FakeDriver([el("flag", "F", value="on"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if then",
                "steps": [
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "flag"}, "equals": "on"}},
                            "then": [{"tap": {"id": "ok"}}],
                            "else": [{"tap": {"id": "missing"}}],
                        },
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ok"}) in driver.actions


def test_if_takes_else_branch() -> None:
    driver = FakeDriver([el("flag", "F", value="off"), el("fallback", "FB", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if else",
                "steps": [
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "flag"}, "equals": "on"}},
                            "then": [{"tap": {"id": "missing"}}],
                            "else": [{"tap": {"id": "fallback"}}],
                        },
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "fallback"}) in driver.actions


def test_if_without_else_skips() -> None:
    driver = FakeDriver([el("flag", "F", value="off")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if no else",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "nonexistent"}},
                            "then": [{"tap": {"id": "missing"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert driver.actions == []


def test_if_condition_is_interpolated() -> None:
    """${vars.*} tokens in if conditions are substituted."""
    driver = FakeDriver([el("x", "X", value="hello"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "if interp",
                "steps": [
                    {"tap": {"id": "x"}, "extract": {"val": {"sel": {"id": "x"}}}},
                    {
                        "if": {
                            "condition": {"value": {"sel": {"id": "x"}, "equals": "${vars.val}"}},
                            "then": [{"tap": {"id": "ok"}}],
                        }
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("tap", {"id": "ok"}) in driver.actions


def test_foreach_iterates() -> None:
    driver = FakeDriver(
        [el("item.a", "A", ["button"]), el("item.b", "B", ["button"]), el("other", "X")]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "current",
                            "steps": [{"tap": {"id": "${vars.current}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    tapped = [a[1] for a in driver.actions if a[0] == "tap"]
    assert tapped == [{"id": "item.a"}, {"id": "item.b"}]


def test_foreach_empty_succeeds() -> None:
    driver = FakeDriver([el("other", "X")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach empty",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"idMatches": "item.*"},
                            "as": "x",
                            "steps": [{"tap": {"id": "${vars.x}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert driver.actions == []


def test_foreach_no_identifier_fails() -> None:
    driver = FakeDriver(
        [{"identifier": None, "label": "L", "traits": [], "value": None, "frame": (0, 0, 10, 10)}]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "forEach no id",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"label": "L"},
                            "as": "x",
                            "steps": [{"tap": {"id": "${vars.x}"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "identifier" in (result.failure or "").lower()


def test_nested_step_indices_are_unique() -> None:
    """Steps inside if/forEach get monotonically increasing indices, not duplicates."""
    driver = FakeDriver(
        [el("flag", "F", value="on"), el("a", "A", ["button"]), el("b", "B", ["button"])]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "unique idx",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "flag"}},
                            "then": [{"tap": {"id": "a"}}],
                        }
                    },
                    {"tap": {"id": "b"}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    indices = [s.index for s in result.steps]
    assert len(indices) == len(set(indices)), f"duplicate indices: {indices}"
