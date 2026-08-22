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


def test_system_alert_handling_default_unset() -> None:
    # On by default, but kept None when unset so a dumped scenario stays clean.
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.system_alert_handling is None
    assert "systemAlertHandling" not in dump_scenarios([s])


def test_system_alert_handling_bool_and_object_forms() -> None:
    off = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": False, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.system_alert_handling is not None
    assert (
        off.system_alert_handling.enabled is False
    )  # bare bool is shorthand for {enabled: <bool>}

    instr = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {"instruction": "tap Allow"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert instr.system_alert_handling is not None
    assert instr.system_alert_handling.enabled is True  # object form stays on unless enabled: false
    assert instr.system_alert_handling.instruction == "tap Allow"

    # The object form round-trips (the bool form normalizes to {enabled: false}).
    rt = load_scenarios(dump_scenarios([instr]))[0]
    assert (
        rt.system_alert_handling is not None and rt.system_alert_handling.instruction == "tap Allow"
    )

    with pytest.raises(ValidationError):  # extra="forbid" rejects unknown keys
        Scenario.model_validate(
            {"name": "x", "systemAlertHandling": {"bogus": 1}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_alert_handling_alias_parses_and_dumps_canonical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The deprecated `alertHandling` key (originally BE-0317's canonical name) parses to the same
    # model as `systemAlertHandling`, and a dump emits the canonical name — so an old scenario keeps
    # working but is rewritten on save.
    import logging

    from bajutsu import deprecations

    deprecations._emitted.discard("scenario.alertHandling")  # so the one-time notice fires here
    with caplog.at_level(logging.WARNING, logger="bajutsu.deprecations"):
        s = Scenario.model_validate(
            {
                "name": "x",
                "alertHandling": {"instruction": "tap Allow"},
                "steps": [{"tap": {"id": "a"}}],
            }
        )
    assert (
        s.system_alert_handling is not None and s.system_alert_handling.instruction == "tap Allow"
    )
    dumped = dump_scenarios([s])
    assert "systemAlertHandling" in dumped and "alertHandling" not in dumped
    assert any("alertHandling" in r.message and "deprecated" in r.message for r in caplog.records)


def test_dismiss_alerts_alias_parses_and_dumps_canonical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # BE-0317: the deprecated `dismissAlerts` key parses to the same model as `systemAlertHandling`,
    # and a dump emits the canonical name — so an old scenario keeps working but is rewritten on save.
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
    assert (
        s.system_alert_handling is not None and s.system_alert_handling.instruction == "tap Allow"
    )
    dumped = dump_scenarios([s])
    assert "systemAlertHandling" in dumped and "dismissAlerts" not in dumped
    assert any("dismissAlerts" in r.message and "deprecated" in r.message for r in caplog.records)


def test_system_alert_handling_instruction_accepts_a_label_list() -> None:
    # BE-0315: the deterministic native form is an ordered list of candidate labels; it round-trips.
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {"instruction": ["Allow", "OK"]},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.system_alert_handling is not None
    assert s.system_alert_handling.instruction == ["Allow", "OK"]
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert rt.system_alert_handling is not None
    assert rt.system_alert_handling.instruction == ["Allow", "OK"]


def test_system_alert_handling_instruction_drops_empty_labels_and_normalizes_to_none() -> None:
    # A list of only blank labels can match nothing deterministically, so it normalizes to the
    # default dismissive policy (None) rather than silently matching zero buttons (BE-0315).
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {"instruction": ["", "  "]},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.system_alert_handling is not None and s.system_alert_handling.instruction is None


def test_system_alert_handling_poll_interval() -> None:
    # BE-0315: the native poll interval is a per-scenario knob; a non-positive value is rejected.
    s = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": {"pollInterval": 2.5}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert s.system_alert_handling is not None and s.system_alert_handling.poll_interval == 2.5
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "systemAlertHandling": {"pollInterval": 0},
                "steps": [{"tap": {"id": "a"}}],
            }
        )


def test_system_alert_handling_rules_default_empty_and_pruned() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.system_alert_handling is None  # unset entirely, same as today

    on = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": {"enabled": True}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert on.system_alert_handling is not None and on.system_alert_handling.rules == []
    assert "rules" not in dump_scenarios([on])  # empty list prunes, like `interrupts`


def test_system_alert_handling_rules_parse_and_round_trip() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {
                "rules": [
                    {"prompt": "notifications", "choice": "grant"},
                    {"prompt": "tracking", "choice": "deny"},
                ]
            },
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.system_alert_handling is not None
    assert [(r.prompt, r.choice) for r in s.system_alert_handling.rules] == [
        ("notifications", "grant"),
        ("tracking", "deny"),
    ]
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert rt.system_alert_handling is not None
    assert [(r.prompt, r.choice) for r in rt.system_alert_handling.rules] == [
        ("notifications", "grant"),
        ("tracking", "deny"),
    ]


def test_system_alert_handling_rules_and_instruction_compose() -> None:
    # rules and instruction are not exclusive: instruction stays the catch-all for whatever
    # prompt no rule names.
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {
                "rules": [{"prompt": "notifications", "choice": "grant"}],
                "instruction": ["Not Now"],
            },
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.system_alert_handling is not None
    assert s.system_alert_handling.instruction == ["Not Now"]
    assert len(s.system_alert_handling.rules) == 1


def test_system_alert_handling_rules_rejects_duplicate_prompt() -> None:
    # Silently taking the first of two rules naming the same prompt would hide an authoring
    # mistake, so it fails at parse time instead — the same reason an ambiguous selector fails.
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "systemAlertHandling": {
                    "rules": [
                        {"prompt": "notifications", "choice": "grant"},
                        {"prompt": "notifications", "choice": "deny"},
                    ]
                },
                "steps": [{"tap": {"id": "a"}}],
            }
        )


def test_system_alert_handling_rules_rejects_unknown_prompt_or_choice() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "systemAlertHandling": {"rules": [{"prompt": "bogus", "choice": "grant"}]},
                "steps": [{"tap": {"id": "a"}}],
            }
        )
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "systemAlertHandling": {"rules": [{"prompt": "notifications", "choice": "bogus"}]},
                "steps": [{"tap": {"id": "a"}}],
            }
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
