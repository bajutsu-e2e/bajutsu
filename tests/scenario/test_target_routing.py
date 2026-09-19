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


def test_use_not_yet_supported_under_two_targets() -> None:
    # BE-0428: expand_components replaces a `use:` step wholesale with the component's own steps,
    # discarding the `use:` step's own `target` — refused outright rather than accepted with a
    # required-looking field that expansion would silently ignore.
    with pytest.raises(ValidationError, match="use: is not yet supported"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "use": {"component": "login.yaml", "with": {}}},
                ],
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


def test_interrupts_not_yet_supported_under_two_targets() -> None:
    # BE-0428: which target an interrupt's condition polls is an open question, so a non-empty
    # `interrupts` is refused outright once the scenario declares two or more targets — before the
    # per-step/condition rules below even run, regardless of whether they would otherwise pass.
    with pytest.raises(ValidationError, match="interrupts is not yet supported"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_step(target="app")],
                "interrupts": [
                    {"condition": {"exists": {"id": "popup"}}, "steps": [_step(target="app")]},
                ],
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
