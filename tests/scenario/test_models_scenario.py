"""Tests for the scenario scenario, preconditions, and alert-guard models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import (
    Scenario,
    dump_scenarios,
    load_scenarios,
)


def test_preconditions_default() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert (
        s.preconditions.erase is None
    )  # unset: inherit target config, then built-in off (BE-0177)
    assert s.preconditions.reinstall == "clean"  # uninstall + install by default


def test_preconditions_reinstall_validated() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "preconditions": {"erase": True, "reinstall": "overwrite"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.preconditions.erase is True and s.preconditions.reinstall == "overwrite"
    with pytest.raises(ValidationError):  # only clean | overwrite are accepted
        Scenario.model_validate(
            {"name": "x", "preconditions": {"reinstall": "bogus"}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_dismiss_alerts_default_unset() -> None:
    # On by default, but kept None when unset so a dumped scenario stays clean.
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.dismiss_alerts is None
    assert "dismissAlerts" not in dump_scenarios([s])


def test_dismiss_alerts_bool_and_object_forms() -> None:
    off = Scenario.model_validate(
        {"name": "x", "dismissAlerts": False, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.dismiss_alerts is not None
    assert off.dismiss_alerts.enabled is False  # bare bool is shorthand for {enabled: <bool>}

    instr = Scenario.model_validate(
        {
            "name": "x",
            "dismissAlerts": {"instruction": "tap Allow"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert instr.dismiss_alerts is not None
    assert instr.dismiss_alerts.enabled is True  # object form stays on unless enabled: false
    assert instr.dismiss_alerts.instruction == "tap Allow"

    # The object form round-trips (the bool form normalizes to {enabled: false}).
    rt = load_scenarios(dump_scenarios([instr]))[0]
    assert rt.dismiss_alerts is not None and rt.dismiss_alerts.instruction == "tap Allow"

    with pytest.raises(ValidationError):  # extra="forbid" rejects unknown keys
        Scenario.model_validate(
            {"name": "x", "dismissAlerts": {"bogus": 1}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_dismiss_alerts_instruction_accepts_a_label_list() -> None:
    # BE-0315: the deterministic native form is an ordered list of candidate labels; it round-trips.
    s = Scenario.model_validate(
        {
            "name": "x",
            "dismissAlerts": {"instruction": ["Allow", "OK"]},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.dismiss_alerts is not None
    assert s.dismiss_alerts.instruction == ["Allow", "OK"]
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert rt.dismiss_alerts is not None and rt.dismiss_alerts.instruction == ["Allow", "OK"]


def test_dismiss_alerts_instruction_drops_empty_labels_and_normalizes_to_none() -> None:
    # A list of only blank labels can match nothing deterministically, so it normalizes to the
    # default dismissive policy (None) rather than silently matching zero buttons (BE-0315).
    s = Scenario.model_validate(
        {"name": "x", "dismissAlerts": {"instruction": ["", "  "]}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert s.dismiss_alerts is not None and s.dismiss_alerts.instruction is None


def test_dismiss_alerts_poll_interval() -> None:
    # BE-0315: the native poll interval is a per-scenario knob; a non-positive value is rejected.
    s = Scenario.model_validate(
        {"name": "x", "dismissAlerts": {"pollInterval": 2.5}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert s.dismiss_alerts is not None and s.dismiss_alerts.poll_interval == 2.5
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "dismissAlerts": {"pollInterval": 0}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_permissions_default_unset() -> None:
    # Empty by default, and pruned when empty so a dumped scenario stays clean (BE-0276).
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.permissions == {}
    assert "permissions" not in dump_scenarios([s])


def test_permissions_parse_and_round_trip() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "permissions": {"camera": "grant", "location": "revoke"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.permissions == {"camera": "grant", "location": "revoke"}

    rt = load_scenarios(dump_scenarios([s]))[0]
    assert rt.permissions == {"camera": "grant", "location": "revoke"}


def test_permissions_rejects_unknown_service() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "permissions": {"bogus": "grant"}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_permissions_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "permissions": {"camera": "bogus"}, "steps": [{"tap": {"id": "a"}}]}
        )
