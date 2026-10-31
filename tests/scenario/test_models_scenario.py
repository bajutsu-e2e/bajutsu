"""Tests for the scenario scenario, preconditions, and alert-guard models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import (
    Interrupt,
    Scenario,
    SystemAlertInterrupt,
    TipKitInterrupt,
    dump_scenarios,
    load_scenarios,
    system_alert_entries,
)


def test_preconditions_default() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.preconditions.subject.reinstall is None  # unset: the target, then the built-in clean


def test_preconditions_reinstall_validated() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "preconditions": {"subject": {"reinstall": "erase"}},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.preconditions.subject.reinstall == "erase"
    with pytest.raises(ValidationError):  # only overwrite | clean | erase are accepted
        Scenario.model_validate(
            {
                "name": "x",
                "preconditions": {"subject": {"reinstall": "bogus"}},
                "steps": [{"tap": {"id": "a"}}],
            }
        )


def _scenario(interrupts: list[object], **extra: object) -> Scenario:
    return Scenario.model_validate(
        {"name": "x", "interrupts": interrupts, "steps": [{"tap": {"id": "a"}}], **extra}
    )


def test_interrupts_default_empty_and_pruned_from_a_dump() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.interrupts == []
    assert "interrupts" not in dump_scenarios([s])


def test_interrupts_each_form_loads_and_round_trips() -> None:
    s = _scenario(
        [
            {"condition": {"exists": {"id": "aerr_wait"}}, "steps": [{"tap": {"id": "close"}}]},
            {"systemAlert": "notifications", "choice": "grant", "pollInterval": 2},
            {"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": 20},
            {"systemAlert": "paste", "choice": "leave"},
            {"tipKit": "dismiss"},
        ]
    )
    assert [type(e) for e in s.interrupts] == [
        Interrupt,
        SystemAlertInterrupt,
        SystemAlertInterrupt,
        SystemAlertInterrupt,
        TipKitInterrupt,
    ]
    assert len(s.screens) == 1
    assert [(e.system_alert, e.choice) for e in system_alert_entries(s.interrupts)] == [
        ("notifications", "grant"),
        ("savePassword", "deny"),
        ("paste", "leave"),
    ]
    assert s.dismisses_tips
    rt = load_scenarios(dump_scenarios([s]))[0]
    assert rt.interrupts == s.interrupts


def test_tip_kit_leave_does_not_dismiss() -> None:
    assert not _scenario([{"tipKit": "leave"}]).dismisses_tips
    assert not _scenario([]).dismisses_tips


@pytest.mark.parametrize(
    ("entry", "forms"),
    [
        (
            {"condition": {"exists": {"id": "a"}}, "systemAlert": "paste", "choice": "deny"},
            "screen and systemAlert",
        ),
        ({"systemAlert": "paste", "choice": "deny", "tipKit": "dismiss"}, "systemAlert and tipKit"),
        ({"tipKit": "dismiss", "steps": []}, "tipKit and screen"),
    ],
)
def test_interrupts_entry_mixing_forms_names_both(entry: dict[str, object], forms: str) -> None:
    with pytest.raises(ValidationError, match=f"mixes the {forms} forms"):
        _scenario([entry])


def test_interrupts_entry_with_no_form_key_names_the_three() -> None:
    with pytest.raises(ValidationError, match="needs one of `condition`"):
        _scenario([{"choice": "grant"}])
    with pytest.raises(ValidationError, match="needs one of `condition`"):
        _scenario([{}])


def test_interrupts_rejects_a_prompt_named_twice() -> None:
    # Silently taking the first of two entries naming the same prompt would hide an authoring
    # mistake, so it fails at parse time instead — the same reason an ambiguous selector fails.
    with pytest.raises(ValidationError, match="notifications more than once"):
        _scenario(
            [
                {"systemAlert": "notifications", "choice": "grant"},
                {"systemAlert": "notifications", "choice": "leave"},
            ]
        )


def test_interrupts_rejects_two_tip_kit_entries() -> None:
    with pytest.raises(ValidationError, match="more than one tipKit entry"):
        _scenario([{"tipKit": "dismiss"}, {"tipKit": "leave"}])


def test_interrupts_rejects_unknown_prompt_choice_or_tip_kit_value() -> None:
    for entry in (
        {"systemAlert": "bogus", "choice": "grant"},
        {"systemAlert": "notifications", "choice": "maybe"},
        {"tipKit": "hide"},
        {"systemAlert": "notifications", "choice": "grant", "bogus": 1},
    ):
        with pytest.raises(ValidationError):
            _scenario([entry])


def test_frozen_screen_timeout_only_on_an_in_tree_prompt() -> None:
    _scenario([{"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": 0}])
    with pytest.raises(ValidationError, match="frozenScreenTimeout applies only"):
        _scenario([{"systemAlert": "notifications", "choice": "grant", "frozenScreenTimeout": 5}])


def test_system_alert_tuning_values_are_validated() -> None:
    with pytest.raises(ValidationError, match="pollInterval must be positive"):
        _scenario([{"systemAlert": "notifications", "choice": "grant", "pollInterval": 0}])
    with pytest.raises(ValidationError, match="must not be negative"):
        _scenario([{"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": -1}])


def test_handle_system_alert_step_rejects_leave() -> None:
    # A step that taps no button has no meaning, so the step keeps grant/deny only (BE-0473).
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "handleSystemAlert": {
                            "prompt": "notifications",
                            "choice": "leave",
                            "timeout": 5,
                        }
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    ("key", "names"),
    [
        ("systemAlertHandling", "- systemAlert: <prompt>"),
        ("alertHandling", "- systemAlert: <prompt>"),
        ("dismissAlerts", "- systemAlert: <prompt>"),
        ("iosTipKitHandling", "- tipKit: dismiss"),
    ],
)
def test_removed_top_level_alert_keys_name_interrupts(key: str, names: str) -> None:
    with pytest.raises(ValidationError, match="interrupts") as exc:
        Scenario.model_validate({"name": "x", key: False, "steps": [{"tap": {"id": "a"}}]})
    assert names in str(exc.value)


@pytest.mark.parametrize(
    ("key", "names"),
    [
        ("erase", "preconditions.subject.reinstall"),
        ("systemAlertHandling", "- systemAlert: <prompt>"),
        ("tipKitHandling", "- tipKit: dismiss"),
        ("inheritSetup", "preconditions.inheritSetup"),
    ],
)
def test_preconditions_run_names_each_new_location(key: str, names: str) -> None:
    with pytest.raises(ValidationError, match=f"preconditions.run.{key} moved to") as exc:
        Scenario.model_validate(
            {"name": "x", "preconditions": {"run": {key: True}}, "steps": [{"tap": {"id": "a"}}]}
        )
    assert names in str(exc.value)


def test_preconditions_run_without_a_known_key_still_fails() -> None:
    with pytest.raises(ValidationError, match=r"preconditions\.run was removed"):
        Scenario.model_validate(
            {"name": "x", "preconditions": {"run": {}}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_preconditions_inherit_setup_defaults_on_and_round_trips() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.preconditions.inherit_setup is True
    off = Scenario.model_validate(
        {"name": "x", "preconditions": {"inheritSetup": False}, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.preconditions.inherit_setup is False
    assert load_scenarios(dump_scenarios([off]))[0].preconditions.inherit_setup is False


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


def test_source_stem_defaults_to_none() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.source_stem is None


def test_source_stem_set_by_loader_is_readable() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    s.set_source_stem("login_flow")
    assert s.source_stem == "login_flow"


def test_source_stem_never_appears_in_model_dump() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    s.set_source_stem("login_flow")
    assert "source_stem" not in s.model_dump()
    assert "sourceStem" not in s.model_dump(by_alias=True)
    assert "source_stem" not in dump_scenarios([s])


# --- group nesting (rejected: a `group` never expands there) ---


def test_group_at_top_level_is_accepted() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "steps": [{"group": {"name": "login", "steps": [{"tap": {"id": "a"}}]}}],
        }
    )
    assert s.steps[0].group is not None


def test_group_nested_inside_another_group_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside a group"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "group": {
                            "name": "outer",
                            "steps": [
                                {
                                    "group": {
                                        "name": "inner",
                                        "steps": [{"tap": {"id": "a"}}],
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        )


_INNER_GROUP = {"name": "inner", "steps": [{"tap": {"id": "a"}}]}


def test_group_nested_inside_an_if_then_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside if"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "c"}},
                            "then": [{"group": _INNER_GROUP}],
                        }
                    }
                ],
            }
        )


def test_group_nested_inside_a_foreach_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside forEach"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "forEach": {
                            "sel": {"id": "c"},
                            "as": "x",
                            "steps": [{"group": _INNER_GROUP}],
                        }
                    }
                ],
            }
        )


def test_group_nested_inside_web_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside web"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [{"web": {"within": {"id": "wv"}, "steps": [{"group": _INNER_GROUP}]}}],
            }
        )


def test_group_nested_inside_app_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside app"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.app",
                            "steps": [{"group": _INNER_GROUP}],
                        }
                    }
                ],
            }
        )


def test_group_nested_inside_an_else_branch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside if"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "c"}},
                            "then": [{"tap": {"id": "a"}}],
                            "else": [
                                {
                                    "group": {
                                        "name": "inner",
                                        "steps": [{"tap": {"id": "b"}}],
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        )


def test_group_nested_inside_before_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside a group"):
        Scenario.model_validate(
            {
                "name": "x",
                "setup": [
                    {
                        "group": {
                            "name": "outer",
                            "steps": [
                                {"group": {"name": "inner", "steps": [{"tap": {"id": "a"}}]}}
                            ],
                        }
                    }
                ],
                "steps": [{"tap": {"id": "z"}}],
            }
        )


def test_group_nested_inside_an_after_rules_steps_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside a group"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [{"tap": {"id": "z"}}],
                "cleanup": [
                    {
                        "on": "always",
                        "steps": [
                            {
                                "group": {
                                    "name": "outer",
                                    "steps": [
                                        {
                                            "group": {
                                                "name": "inner",
                                                "steps": [{"tap": {"id": "a"}}],
                                            }
                                        }
                                    ],
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_group_nested_inside_an_interrupts_entrys_steps_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not nest inside a group"):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [{"tap": {"id": "z"}}],
                "interrupts": [
                    {
                        "condition": {"exists": {"id": "dialog"}},
                        "steps": [
                            {
                                "group": {
                                    "name": "outer",
                                    "steps": [
                                        {
                                            "group": {
                                                "name": "inner",
                                                "steps": [{"tap": {"id": "a"}}],
                                            }
                                        }
                                    ],
                                }
                            }
                        ],
                    }
                ],
            }
        )


def test_set_mocks_takes_scenario_mock_entries_and_an_empty_table() -> None:
    """`setMocks` (BE-0365 unit 4) reuses the scenario-level `mocks` entry shape, and `[]` — remove
    every stub — is a valid table rather than a missing action."""
    s = Scenario.model_validate(
        {
            "name": "x",
            "steps": [
                {"setMocks": [{"match": {"path": "/me"}, "respond": {"status": 500}}]},
                {"setMocks": []},
            ],
        }
    )
    assert s.steps[0].set_mocks is not None and s.steps[0].set_mocks[0].respond.status == 500
    assert s.steps[1].set_mocks == []
    # Round-trips under its DSL name.
    assert load_scenarios(dump_scenarios([s]))[0].steps[1].set_mocks == []


def test_set_mocks_obeys_the_one_action_rule_and_validates_its_entries() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Scenario.model_validate({"name": "x", "steps": [{"setMocks": [], "tap": {"id": "a"}}]})
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {"name": "x", "steps": [{"setMocks": [{"respond": {"status": 500}}]}]}
        )


def test_a_non_mapping_scenario_fails_as_a_type_error() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate("x")
