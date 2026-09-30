"""Tests for `Scenario.targets` / `Step.target` / `Assertion.target` (BE-0428).

Covers the `Scenario`-level validator (`_check_target_requirements`) across zero/one/two-or-more
`targets`, on `steps`/`before`/`after`/`expect`/`interrupts` and every nested `if`/`forEach`/`web`
shape, duplicate-name rejection, and the `Assertion.target`-outside-`expect` rejection. The
`expand_components`/`with_lifecycle_phases` re-check survival lives in `tests/test_components.py`
and `tests/orchestrator/test_before_after.py`, alongside the mutation points themselves.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import Scenario


def _step(**overrides: object) -> dict[str, object]:
    return {"tap": {"id": "a"}, **overrides}


# --- zero declared targets: today's scenario, unchanged --------------------------------------


def test_zero_targets_step_target_optional() -> None:
    s = Scenario.model_validate({"name": "s", "steps": [_step()]})
    assert s.targets == []
    assert s.steps[0].target is None


def test_zero_targets_rejects_a_step_target() -> None:
    with pytest.raises(ValidationError, match="declares no targets"):
        Scenario.model_validate({"name": "s", "steps": [_step(target="app")]})


def test_zero_targets_rejects_an_expect_target() -> None:
    with pytest.raises(ValidationError, match="declares no targets"):
        Scenario.model_validate(
            {
                "name": "s",
                "steps": [_step()],
                "expect": [{"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "app"}],
            }
        )


# --- one declared target: a step may omit it, or must name that one --------------------------


def test_one_target_step_may_omit_target() -> None:
    s = Scenario.model_validate({"name": "s", "targets": ["app"], "steps": [_step()]})
    assert s.steps[0].target is None


def test_one_target_step_may_name_the_declared_target() -> None:
    s = Scenario.model_validate({"name": "s", "targets": ["app"], "steps": [_step(target="app")]})
    assert s.steps[0].target == "app"


def test_one_target_step_naming_a_different_target_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        Scenario.model_validate({"name": "s", "targets": ["app"], "steps": [_step(target="web")]})


# --- two or more declared targets: every action step and expect entry must set target --------


def test_two_targets_step_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate({"name": "s", "targets": ["app", "web"], "steps": [_step()]})


def test_two_targets_step_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="not one of"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "web"], "steps": [_step(target="android")]}
        )


def test_two_targets_every_step_named_passes() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [_step(target="app"), _step(target="web")],
        }
    )
    assert [st.target for st in s.steps] == ["app", "web"]


def test_two_targets_expect_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_step(target="app")],
                "expect": [{"value": {"sel": {"id": "a"}, "equals": "1"}}],
            }
        )


def test_two_targets_expect_with_target_passes() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [_step(target="app")],
            "expect": [
                {"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "web"},
            ],
        }
    )
    assert s.expect[0].target == "web"


def test_two_targets_expect_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="not one of"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_step(target="app")],
                "expect": [
                    {"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "android"},
                ],
            }
        )


def test_one_target_expect_naming_the_declared_target_passes() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app"],
            "steps": [_step()],
            "expect": [
                {"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "app"},
            ],
        }
    )
    assert s.expect[0].target == "app"


def test_one_target_expect_naming_a_different_target_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app"],
                "steps": [_step()],
                "expect": [
                    {"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "web"},
                ],
            }
        )


# --- duplicate target names ---------------------------------------------------------------


def test_duplicate_target_name_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "app"], "steps": [_step(target="app")]}
        )


# --- before / after / interrupts walked the same way as steps --------------------------------


def test_before_walked_the_same_way() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "before": [_step()],
                "steps": [_step(target="app")],
            }
        )


def test_after_walked_the_same_way() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_step(target="app")],
                "after": [{"on": "always", "steps": [_step()]}],
            }
        )


def test_use_may_omit_target_under_two_targets() -> None:
    # BE-0446: the expanded steps resolve for themselves, after `expand_components`, so the `use:`
    # step itself is neither required to name a target nor resolved to one.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [{"use": {"component": "login.yaml", "with": {}}}],
        }
    )
    assert s.steps[0].target is None
    assert s.steps[0].resolved_target is None


def test_use_may_name_a_declared_target_under_two_targets() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [{"target": "app", "use": {"component": "login.yaml", "with": {}}}],
        }
    )
    assert s.steps[0].target == "app"


def test_use_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="target 'ios' is not one of"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{"target": "ios", "use": {"component": "login.yaml", "with": {}}}],
            }
        )


def test_use_allowed_under_one_target() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app"],
            "steps": [{"use": {"component": "login.yaml", "with": {}}}],
        }
    )
    assert s.steps[0].use is not None


def test_group_naming_target_loads_under_two_targets_with_no_primary() -> None:
    # BE-0446: the group's children are left to the post-expansion pass, which sees them stamped
    # with the group's target; checking them here would reject this valid group.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                {"target": "app", "group": {"name": "login", "steps": [_step()]}},
            ],
        }
    )
    assert s.steps[0].target == "app"


def test_group_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="target 'ios' is not one of"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{"target": "ios", "group": {"name": "login", "steps": [_step()]}}],
            }
        )


def test_group_allowed_under_one_target() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app"],
            "steps": [{"group": {"name": "login", "steps": [_step()]}}],
        }
    )
    assert s.steps[0].group is not None


def test_group_steps_are_checked_under_one_target() -> None:
    # A `group`'s own inner steps are walked at load time too (BE-0446), so a static reader that
    # never expands (`bajutsu lint`) still sees the usual per-step target rule.
    with pytest.raises(ValidationError, match="does not match"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app"],
                "steps": [
                    {"group": {"name": "login", "steps": [_step(target="other")]}},
                ],
            }
        )


def test_group_omitting_target_still_requires_one_on_its_children() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{"group": {"name": "login", "steps": [_step()]}}],
            }
        )


def test_group_child_naming_a_different_target_rejected_at_load_time() -> None:
    with pytest.raises(ValidationError, match="conflicts with the enclosing group's target 'app'"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "group": {"name": "login", "steps": [_step(target="web")]}}
                ],
            }
        )


def _two_targets_with(entry: dict[str, object]) -> Scenario:
    return Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [_step(target="app")],
            "interrupts": [{"condition": {"exists": {"id": "popup"}}, **entry}],
        }
    )


def test_interrupts_entry_may_omit_target_under_two_targets() -> None:
    # BE-0438: unlike a step's, an entry's `target` stays optional at any count — an omitted one
    # watches the primary — and so does a recovery step's, which runs on the entry's own target.
    s = _two_targets_with({"steps": [_step()]})
    assert s.interrupts[0].target is None
    assert s.interrupts[0].steps[0].target is None


def test_interrupts_entry_may_name_a_declared_target() -> None:
    s = _two_targets_with({"target": "web", "steps": [_step()]})
    assert s.interrupts[0].target == "web"


def test_interrupts_entry_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="interrupts entry: target 'ios' is not one of"):
        _two_targets_with({"target": "ios", "steps": [_step()]})


def test_interrupts_recovery_step_may_name_another_declared_target() -> None:
    s = _two_targets_with({"target": "web", "steps": [_step(target="app")]})
    assert s.interrupts[0].steps[0].target == "app"


def test_interrupts_recovery_step_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="target 'ios' is not one of"):
        _two_targets_with({"steps": [_step(target="ios")]})


def test_interrupts_recovery_optional_mode_propagates_through_if() -> None:
    # The optional mode reaches a nested `if` branch, which at the top level would require a
    # target of its own under two targets.
    s = _two_targets_with(
        {"steps": [{"if": {"condition": {"exists": {"id": "a"}}, "then": [_step()]}}]}
    )
    assert s.interrupts[0].steps[0].if_ is not None


def test_interrupts_recovery_step_inside_web_still_rejects_a_target() -> None:
    with pytest.raises(ValidationError, match="nested inside a web: or app: block"):
        _two_targets_with(
            {"steps": [{"web": {"within": {"id": "wv"}, "steps": [_step(target="app")]}}]}
        )


def test_interrupts_recovery_use_loads_under_two_targets() -> None:
    # BE-0446: a recovery `use:` may omit `target`, like any recovery step.
    s = _two_targets_with({"steps": [{"use": {"component": "c.yaml", "with": {}}}]})
    assert s.interrupts[0].steps[0].use is not None


def test_interrupts_entry_target_rejected_with_no_declared_targets() -> None:
    with pytest.raises(ValidationError, match="interrupts entry: target is set"):
        Scenario.model_validate(
            {
                "name": "s",
                "steps": [_step()],
                "interrupts": [{"target": "app", "condition": {"exists": {"id": "popup"}}}],
            }
        )


def test_interrupts_entry_target_must_match_the_one_declared_target() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app"],
                "steps": [_step()],
                "interrupts": [{"target": "web", "condition": {"exists": {"id": "popup"}}}],
            }
        )


def test_interrupts_steps_walked_the_same_way_under_one_target() -> None:
    # The granular per-step rule still applies below the two-target threshold, where `interrupts`
    # remains supported: a step naming a target other than the one declared is still rejected.
    with pytest.raises(ValidationError, match="does not match"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app"],
                "steps": [_step()],
                "interrupts": [
                    {"condition": {"exists": {"id": "popup"}}, "steps": [_step(target="web")]},
                ],
            }
        )


def test_interrupts_condition_rejects_a_target_under_one_target() -> None:
    with pytest.raises(ValidationError, match="only allowed on a top-level expect"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app"],
                "steps": [_step()],
                "interrupts": [
                    {
                        "condition": {"exists": {"id": "popup"}, "target": "app"},
                        "steps": [_step()],
                    },
                ],
            }
        )


# --- nested if / forEach: as required as a top-level step, three levels deep -----------------


def test_if_step_itself_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "a"}},
                            "then": [_step(target="app")],
                        }
                    },
                ],
            }
        )


def test_if_condition_rejects_a_target() -> None:
    with pytest.raises(ValidationError, match="only allowed on a top-level expect"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "if": {
                            "condition": {"exists": {"id": "a"}, "target": "app"},
                            "then": [_step(target="app")],
                        },
                    },
                ],
            }
        )


def test_nested_steps_inside_if_then_and_else_require_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "if": {
                            "condition": {"exists": {"id": "a"}},
                            "then": [_step(target="app")],
                            "else": [_step()],  # missing target
                        },
                    },
                ],
            }
        )


def test_for_each_step_itself_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "forEach": {
                            "sel": {"idMatches": "row.*"},
                            "as": "row",
                            "steps": [_step(target="app")],
                        }
                    },
                ],
            }
        )


def test_a_step_three_levels_deep_inside_for_each_requires_target() -> None:
    # A forEach nested inside an if, nested inside a top-level forEach — the innermost step is
    # exactly as required to declare target as one at the top level.
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "forEach": {
                            "sel": {"idMatches": "row.*"},
                            "as": "row",
                            "steps": [
                                {
                                    "target": "app",
                                    "if": {
                                        "condition": {"exists": {"id": "a"}},
                                        "then": [_step()],  # three levels deep, missing target
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        )


# --- web: nested steps must omit target outright ----------------------------------------------


def test_web_step_itself_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [{"tap": {"id": "btn"}}],
                        }
                    },
                ],
            }
        )


def test_web_nested_step_rejects_a_target() -> None:
    with pytest.raises(ValidationError, match="not allowed on a step nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [_step(target="web")],  # must omit, not just match
                        },
                    },
                ],
            }
        )


def test_if_nested_inside_web_still_rejects_a_target_transitively() -> None:
    # The `inside_web` flag must propagate through an `if`'s `then`/`else`, not reset at the first
    # nesting level — a regression here would silently start requiring (rather than rejecting)
    # `target` on a step two levels inside a `web:` block, while every test above it stayed green.
    with pytest.raises(ValidationError, match="not allowed on a step nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [
                                {
                                    "if": {
                                        "condition": {"exists": {"id": "a"}},
                                        "then": [_step(target="web")],  # must omit, not match
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        )


def test_for_each_nested_inside_web_still_rejects_a_target_transitively() -> None:
    with pytest.raises(ValidationError, match="not allowed on a step nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [
                                {
                                    "forEach": {
                                        "sel": {"idMatches": "row.*"},
                                        "as": "row",
                                        "steps": [_step(target="web")],  # must omit, not match
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        )


def test_if_else_nested_inside_web_still_rejects_a_target_transitively() -> None:
    with pytest.raises(ValidationError, match="not allowed on a step nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [
                                {
                                    "if": {
                                        "condition": {"exists": {"id": "a"}},
                                        "then": [{"tap": {"id": "a"}}],
                                        "else": [_step(target="web")],  # must omit, not match
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        )


def test_web_nested_step_with_no_target_passes() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                {
                    "target": "web",
                    "web": {
                        "within": {"id": "webview"},
                        "steps": [{"tap": {"id": "btn"}}],
                    },
                },
            ],
        }
    )
    assert s.steps[0].web is not None
    assert s.steps[0].web.steps[0].target is None


# --- Assertion.target is legal only through expect --------------------------------------------


def test_inline_assert_rejects_a_target() -> None:
    with pytest.raises(ValidationError, match="only allowed on a top-level expect"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "assert": [{"value": {"sel": {"id": "a"}, "equals": "1"}, "target": "app"}],
                    },
                ],
            }
        )


def test_inline_assert_with_no_target_passes() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                {
                    "target": "app",
                    "assert": [{"value": {"sel": {"id": "a"}, "equals": "1"}}],
                },
            ],
        }
    )
    assert s.steps[0].assert_ is not None
    assert s.steps[0].assert_[0].target is None
