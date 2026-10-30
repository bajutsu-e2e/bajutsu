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


def _scenario(**fields: object) -> Scenario:
    return Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}], **fields})


def test_interrupts_take_three_forms_decided_by_their_keys() -> None:
    # BE-0473: an entry's form comes from the key it writes, never from a value's type.
    s = _scenario(
        interrupts=[
            {"condition": {"exists": {"id": ["x"]}}, "steps": [{"tap": {"id": "y"}}]},
            {"systemAlert": "notifications", "choice": "grant"},
            {"tipKit": "dismiss"},
        ]
    )
    screen, alert, tip = s.interrupts
    assert isinstance(screen, Interrupt)
    assert isinstance(alert, SystemAlertInterrupt)
    assert (alert.system_alert, alert.choice) == ("notifications", "grant")
    assert isinstance(tip, TipKitInterrupt)
    assert tip.tip_kit == "dismiss"


def test_interrupt_entries_round_trip_through_a_dump() -> None:
    s = _scenario(
        interrupts=[
            {"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": 20},
            {"systemAlert": "paste", "choice": "leave", "pollInterval": 2.5},
            {"tipKit": "leave"},
        ]
    )
    assert load_scenarios(dump_scenarios([s]))[0].interrupts == s.interrupts


def test_an_entry_mixing_two_forms_fails_naming_both() -> None:
    with pytest.raises(
        ValidationError,
        match=r"mixes `systemAlert` \(choice, systemAlert\) and `tipKit` \(tipKit\)",
    ):
        _scenario(interrupts=[{"systemAlert": "paste", "choice": "grant", "tipKit": "dismiss"}])
    with pytest.raises(
        ValidationError, match=r"mixes a screen entry \(condition\) and `systemAlert`"
    ):
        _scenario(
            interrupts=[
                {"condition": {"exists": {"id": ["x"]}}, "systemAlert": "paste", "choice": "grant"}
            ]
        )


def test_a_screen_entry_without_a_condition_fails_on_the_condition() -> None:
    with pytest.raises(ValidationError, match="condition"):
        _scenario(interrupts=[{"steps": [{"tap": {"id": "y"}}]}])


@pytest.mark.parametrize(
    "entry",
    [
        {"systemAlert": "bogus", "choice": "grant"},
        {"systemAlert": "notifications", "choice": "bogus"},
        {"systemAlert": "notifications"},
        {"tipKit": "on"},
        {"systemAlert": "notifications", "choice": "grant", "pollInterval": 0},
        {"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": -1},
        {"systemAlert": "savePassword", "choice": "leave", "frozenScreenTimeout": 5},
    ],
)
def test_a_malformed_entry_fails_at_load(entry: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _scenario(interrupts=[entry])


def test_frozen_screen_timeout_sits_only_on_an_in_tree_prompt() -> None:
    # The frozen-screen stop arms only for a prompt the app's own tree carries, so the value on a
    # SpringBoard prompt would load and never apply. 0 is allowed: it turns the stop off.
    for value in (20, 0):
        (entry,) = _scenario(
            interrupts=[
                {"systemAlert": "savePassword", "choice": "deny", "frozenScreenTimeout": value}
            ]
        ).interrupts
        assert isinstance(entry, SystemAlertInterrupt)
        assert entry.frozen_screen_timeout == value
    with pytest.raises(ValidationError, match="answered in the app's own tree"):
        _scenario(
            interrupts=[
                {"systemAlert": "notifications", "choice": "deny", "frozenScreenTimeout": 5}
            ]
        )


def test_one_layer_answers_a_prompt_once() -> None:
    # Taking the first of two entries for one prompt would hide an authoring mistake, the same
    # reason an ambiguous selector fails.
    with pytest.raises(ValidationError, match="names systemAlert notifications more than once"):
        _scenario(
            interrupts=[
                {"systemAlert": "notifications", "choice": "grant"},
                {"systemAlert": "notifications", "choice": "leave"},
            ]
        )
    with pytest.raises(ValidationError, match="more than one tipKit entry"):
        _scenario(interrupts=[{"tipKit": "dismiss"}, {"tipKit": "leave"}])


@pytest.mark.parametrize(
    ("run", "new_home"),
    [
        ({"systemAlertHandling": False}, "choice: leave"),
        ({"systemAlertHandling": {"rules": []}}, "systemAlert"),
        ({"tipKitHandling": True}, "tipKit: dismiss"),
    ],
)
def test_a_moved_run_field_fails_naming_its_interrupts_entry(
    run: dict[str, object], new_home: str
) -> None:
    # BE-0473: the loader reads no old key, so the error is the migration path.
    with pytest.raises(ValidationError, match="BE-0473") as exc:
        _scenario(preconditions={"run": run})
    assert new_home in str(exc.value)


@pytest.mark.parametrize("old", ["alertHandling", "dismissAlerts"])
def test_renamed_alert_keys_fail_naming_the_canonical_key(old: str) -> None:
    # BE-0317 / BE-0327 renamed the field twice and kept both spellings as aliases; BE-0401 deleted
    # them, and BE-0473 moved the declaration into `interrupts`, which each now names.
    with pytest.raises(ValidationError) as exc:
        _scenario(**{old: {"rules": [{"prompt": "notifications", "choice": "grant"}]}})
    assert "interrupts" in str(exc.value)


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
