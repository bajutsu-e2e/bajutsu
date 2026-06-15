"""Tests for JSON Schema generation (bajutsu schema)."""

from __future__ import annotations

import json

from bajutsu.lint import scenario_json_schema


def test_schema_is_valid_json_with_anyof() -> None:
    parsed = json.loads(scenario_json_schema())
    assert "anyOf" in parsed
    assert "$defs" in parsed


def test_schema_references_scenario() -> None:
    parsed = json.loads(scenario_json_schema())
    all_text = json.dumps(parsed)
    assert "Scenario" in all_text


def test_schema_includes_step_actions() -> None:
    parsed = json.loads(scenario_json_schema())
    all_text = json.dumps(parsed)
    for action in ("tap", "doubleTap", "longPress", "swipe", "wait"):
        assert action in all_text, f"missing {action} in schema"
