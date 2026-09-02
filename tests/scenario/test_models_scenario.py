"""Tests for the scenario scenario, preconditions, and alert-guard models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import (
    Scenario,
    SystemAlertHandling,
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
    # BE-0401: the boolean carries on and off, so a mapping always means on.
    off = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": False, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.system_alert_handling is False

    on = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": True, "steps": [{"tap": {"id": "a"}}]}
    )
    assert on.system_alert_handling == SystemAlertHandling()  # `true` is the empty policy

    instr = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {"visionInstruction": "tap Allow"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert isinstance(instr.system_alert_handling, SystemAlertHandling)
    assert instr.system_alert_handling.vision_instruction == "tap Allow"

    rt = load_scenarios(dump_scenarios([instr]))[0]
    assert isinstance(rt.system_alert_handling, SystemAlertHandling)
    assert rt.system_alert_handling.vision_instruction == "tap Allow"

    with pytest.raises(ValidationError):  # extra="forbid" rejects unknown keys
        Scenario.model_validate(
            {"name": "x", "systemAlertHandling": {"bogus": 1}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_system_alert_handling_off_round_trips_as_the_bare_boolean() -> None:
    # `false` is now a value of the field itself, not a `{ enabled: false }` mapping, so a dumped
    # scenario must still say `false` — a dump that dropped it would silently re-enable the guard.
    off = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": False, "steps": [{"tap": {"id": "a"}}]}
    )
    dumped = dump_scenarios([off])
    assert "systemAlertHandling: false" in dumped
    assert load_scenarios(dumped)[0].system_alert_handling is False


@pytest.mark.parametrize(
    ("policy", "replacement"),
    [
        ({"instruction": ["Allow"]}, "labels"),
        ({"instruction": "tap Allow"}, "visionInstruction"),
        ({"enabled": False}, "systemAlertHandling: false"),
    ],
)
def test_system_alert_handling_removed_keys_name_their_replacement(
    policy: dict[str, object], replacement: str
) -> None:
    # BE-0401 removed `instruction` and `enabled` with no alias, so the load error is the whole
    # migration path an author gets — `extra="forbid"`'s generic message would name no replacement.
    with pytest.raises(ValidationError) as exc:
        Scenario.model_validate(
            {"name": "x", "systemAlertHandling": policy, "steps": [{"tap": {"id": "a"}}]}
        )
    assert replacement in str(exc.value)


def test_system_alert_handling_rejects_a_non_mapping_value() -> None:
    # The removed-key check runs `mode="before"`, so it also sees a value that is neither the
    # boolean nor a mapping — `systemAlertHandling: on-please`, say. It must fall through to
    # Pydantic's own union error rather than raising on the membership test.
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "systemAlertHandling": "on-please", "steps": [{"tap": {"id": "a"}}]}
        )


@pytest.mark.parametrize("old", ["alertHandling", "dismissAlerts"])
def test_renamed_alert_keys_fail_naming_the_canonical_key(old: str) -> None:
    # BE-0317 / BE-0327 renamed the field twice and kept both spellings as aliases; BE-0401 deleted
    # them, so each now fails to load pointing at `systemAlertHandling`.
    with pytest.raises(ValidationError) as exc:
        Scenario.model_validate(
            {"name": "x", old: {"labels": ["Allow"]}, "steps": [{"tap": {"id": "a"}}]}
        )
    assert "systemAlertHandling" in str(exc.value)


def test_system_alert_handling_labels_accept_an_ordered_list() -> None:
    # BE-0401: `labels` is the native path's ordered candidate list; it round-trips.
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {"labels": ["Allow", "OK"]},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert s.system_alert_handling.labels == ["Allow", "OK"]
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert isinstance(rt.system_alert_handling, SystemAlertHandling)
    assert rt.system_alert_handling.labels == ["Allow", "OK"]


@pytest.mark.parametrize(
    "policy",
    [{"labels": []}, {"labels": ["Allow", "  "]}, {"visionInstruction": "  "}],
)
def test_system_alert_handling_rejects_empty_values(policy: dict[str, object]) -> None:
    # BE-0401: each of these used to normalize away silently and fall through to the default
    # dismissive policy — answering the opposite of what the author wrote. Fail instead, the same
    # reason an ambiguous selector fails rather than tapping its first match.
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "systemAlertHandling": policy, "steps": [{"tap": {"id": "a"}}]}
        )


def test_system_alert_handling_poll_interval() -> None:
    # BE-0315: the native poll interval is a per-scenario knob; a non-positive value is rejected.
    s = Scenario.model_validate(
        {"name": "x", "systemAlertHandling": {"pollInterval": 2.5}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert s.system_alert_handling.poll_interval == 2.5
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
        {"name": "x", "systemAlertHandling": True, "steps": [{"tap": {"id": "a"}}]}
    )
    assert isinstance(on.system_alert_handling, SystemAlertHandling)
    assert on.system_alert_handling.rules == []
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
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert [(r.prompt, r.choice) for r in s.system_alert_handling.rules] == [
        ("notifications", "grant"),
        ("tracking", "deny"),
    ]
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert isinstance(rt.system_alert_handling, SystemAlertHandling)
    assert [(r.prompt, r.choice) for r in rt.system_alert_handling.rules] == [
        ("notifications", "grant"),
        ("tracking", "deny"),
    ]


def test_system_alert_handling_rules_and_labels_compose() -> None:
    # rules and labels are not exclusive: labels stay the catch-all for whatever prompt no rule
    # names, since a prompt name is the more specific declaration.
    s = Scenario.model_validate(
        {
            "name": "x",
            "systemAlertHandling": {
                "rules": [{"prompt": "notifications", "choice": "grant"}],
                "labels": ["Not Now"],
            },
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert isinstance(s.system_alert_handling, SystemAlertHandling)
    assert s.system_alert_handling.labels == ["Not Now"]
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
