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


def test_alert_handling_default_unset() -> None:
    # On by default, but kept None when unset so a dumped scenario stays clean.
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.alert_handling is None
    assert "alertHandling" not in dump_scenarios([s])


def test_alert_handling_bool_and_object_forms() -> None:
    off = Scenario.model_validate(
        {"name": "x", "alertHandling": False, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.alert_handling is not None
    assert off.alert_handling.enabled is False  # bare bool is shorthand for {enabled: <bool>}

    instr = Scenario.model_validate(
        {
            "name": "x",
            "alertHandling": {"instruction": "tap Allow"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert instr.alert_handling is not None
    assert instr.alert_handling.enabled is True  # object form stays on unless enabled: false
    assert instr.alert_handling.instruction == "tap Allow"

    # The object form round-trips (the bool form normalizes to {enabled: false}).
    rt = load_scenarios(dump_scenarios([instr]))[0]
    assert rt.alert_handling is not None and rt.alert_handling.instruction == "tap Allow"

    with pytest.raises(ValidationError):  # extra="forbid" rejects unknown keys
        Scenario.model_validate(
            {"name": "x", "alertHandling": {"bogus": 1}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_dismiss_alerts_alias_parses_and_dumps_canonical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # BE-0317: the deprecated `dismissAlerts` key parses to the same model as `alertHandling`, and a
    # dump emits the canonical name — so an old scenario keeps working but is rewritten on save.
    import logging

    from bajutsu import deprecations

    deprecations._emitted.discard("scenario.dismissAlerts")  # so the one-time notice fires here
    with caplog.at_level(logging.WARNING, logger="bajutsu.deprecations"):
        s = Scenario.model_validate(
            {
                "name": "x",
                "dismissAlerts": {"instruction": "tap Allow"},
                "steps": [{"tap": {"id": "a"}}],
            }
        )
    assert s.alert_handling is not None and s.alert_handling.instruction == "tap Allow"
    dumped = dump_scenarios([s])
    assert "alertHandling" in dumped and "dismissAlerts" not in dumped
    assert any("dismissAlerts" in r.message and "deprecated" in r.message for r in caplog.records)


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
