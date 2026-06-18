"""Tests for extract (vars.*) capture during a run."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


def test_extract_captures_value_into_vars() -> None:
    """extract captures a UI value and makes it available as ${vars.*} for subsequent steps."""
    driver = FakeDriver(
        [
            el("counter.inc", "+", ["button"]),
            el("counter.value", "Count", value="42"),
        ]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract test",
                "steps": [
                    {
                        "tap": {"id": "counter.inc"},
                        "extract": {"count": {"sel": {"id": "counter.value"}}},
                    },
                    {
                        "assert": [
                            {"value": {"sel": {"id": "counter.value"}, "equals": "${vars.count}"}}
                        ]
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_label_prop() -> None:
    driver = FakeDriver([el("title", "Hello World", ["staticText"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract label",
                "steps": [
                    {
                        "tap": {"id": "title"},
                        "extract": {"heading": {"sel": {"id": "title"}, "prop": "label"}},
                    },
                    {"assert": [{"label": {"sel": {"id": "title"}, "equals": "${vars.heading}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_fails_when_selector_not_found() -> None:
    driver = FakeDriver([el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract missing",
                "steps": [{"tap": {"id": "ok"}, "extract": {"x": {"sel": {"id": "nonexistent"}}}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "extract" in (result.failure or "").lower()


def test_extract_skipped_on_failed_step() -> None:
    driver = FakeDriver([el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract on fail",
                "steps": [{"tap": {"id": "missing"}, "extract": {"x": {"sel": {"id": "ok"}}}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "missing" in (result.failure or "")


def test_extract_value_used_in_type_step() -> None:
    driver = FakeDriver(
        [el("source", "Source", value="hello"), el("target", "Target", ["textField"])]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract into type",
                "steps": [
                    {"tap": {"id": "source"}, "extract": {"msg": {"sel": {"id": "source"}}}},
                    {"type": {"text": "${vars.msg}", "into": {"id": "target"}}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert ("type", "hello") in driver.actions


def test_extract_in_scenario_expect() -> None:
    driver = FakeDriver([el("counter", "Count", value="7"), el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract in expect",
                "steps": [{"tap": {"id": "ok"}, "extract": {"n": {"sel": {"id": "counter"}}}}],
                "expect": [{"value": {"sel": {"id": "counter"}, "equals": "${vars.n}"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


def test_extract_selector_is_interpolated() -> None:
    """Tokens in extract selectors are substituted via bindings."""
    driver = FakeDriver([el("ok", "OK", ["button"]), el("target", "T", value="99")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "interp extract",
                "steps": [
                    {
                        "tap": {"id": "ok"},
                        "extract": {"val": {"sel": {"id": "${secrets.sel}"}}},
                    },
                    {"assert": [{"value": {"sel": {"id": "target"}, "equals": "${vars.val}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        bindings={"secrets.sel": "target"},
    )
    assert result.ok, result.failure


def test_expect_failure() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "missing"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("expect:")
