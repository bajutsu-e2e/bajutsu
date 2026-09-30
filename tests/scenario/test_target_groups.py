"""Tests for target groups (BE-0437): `Step.steps`, and their load-time expansion.

Covers the `Step`-level validator (target required unconditionally; capture/extract/name/from_
forbidden; a direct child must omit target, which also rejects a nested group; refused inside
`web:`/`app:`) and `_expand_target_groups` — that a group-authored scenario produces the same
flat, stamped step list a hand-written one would, on every step list it walks (`steps`, `before`,
`after`, `interrupts`), through `if`/`forEach`/`web`/`app` nesting, and that the expansion survives
`expand_components`, `apply_setups`, and config hook-folding the same way `_check_target_requirements`
already does (see `test_target_routing.py`, `test_components.py`, `tests/orchestrator/test_before_after.py`).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.config import load_config, resolve
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.common.scenario import Component, Scenario, expand_components, load_scenarios
from bajutsu.common.scenario.expand import apply_setups
from bajutsu.common.scenario.models import Step


def _step(**overrides: object) -> dict[str, object]:
    return {"tap": {"id": "a"}, **overrides}


def _tap_id(step: Step) -> object:
    assert step.tap is not None
    return step.tap.id


def _group(target: str, steps: list[dict[str, object]]) -> dict[str, object]:
    return {"target": target, "steps": steps}


# --- schema: a target group requires target, forbids the leaf modifiers -----------------------


def test_group_requires_target() -> None:
    with pytest.raises(ValidationError, match="target is required on a target group"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "web"], "steps": [{"steps": [_step()]}]}
        )


def test_group_requires_target_even_with_one_declared_target() -> None:
    # Unconditional, unlike a leaf action's own target (BE-0428) — a group's whole purpose is
    # naming a target once, so it needs one whatever the scenario declares.
    with pytest.raises(ValidationError, match="target is required on a target group"):
        Scenario.model_validate({"name": "s", "targets": ["app"], "steps": [{"steps": [_step()]}]})


def test_group_requires_target_even_with_no_declared_targets() -> None:
    with pytest.raises(ValidationError, match="target is required on a target group"):
        Scenario.model_validate({"name": "s", "steps": [{"steps": [_step()]}]})


@pytest.mark.parametrize(
    ("field", "value"), [("capture", ["screenshot"]), ("extract", {"x": {"sel": {"id": "a"}}})]
)
def test_group_forbids_capture_and_extract(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match="is not supported on a target group"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{**_group("app", [_step()]), field: value}],
            }
        )


def test_group_forbids_name() -> None:
    with pytest.raises(ValidationError, match="is not supported on a target group"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{**_group("app", [_step()]), "name": "my group"}],
            }
        )


def test_group_forbids_from() -> None:
    with pytest.raises(ValidationError, match="is not supported on a target group"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{**_group("app", [_step()]), "from": "tap the like button"}],
            }
        )


def test_group_child_setting_its_own_target_rejected() -> None:
    with pytest.raises(ValidationError, match="must omit target"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_group("app", [_step(target="app")])],
            }
        )


def test_nested_group_rejected_with_a_dedicated_message() -> None:
    # A nested group always sets its own target (it is required unconditionally, above) — exactly
    # what an immediate child may never do. A dedicated check names this case rather than letting
    # it fall through to the generic child-omission message below.
    with pytest.raises(ValidationError, match="cannot nest directly inside another target group"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [_group("app", [_group("web", [_step()])])],
            }
        )


def test_group_cannot_combine_with_a_leaf_action() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [{"target": "app", "tap": {"id": "a"}, "steps": [_step()]}],
            }
        )


def test_group_with_empty_steps_rejected() -> None:
    with pytest.raises(ValidationError, match="steps must not be empty"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "web"], "steps": [_group("app", [])]}
        )


def test_group_with_an_empty_string_target_rejected() -> None:
    with pytest.raises(ValidationError, match="target is required on a target group"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "web"], "steps": [_group("", [_step()])]}
        )


# --- a group's own target is validated against the scenario's declared targets, once stamped --


def test_group_target_not_among_two_or_more_declared_targets_rejected() -> None:
    with pytest.raises(ValidationError, match="not one of the scenario's declared targets"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app", "web"], "steps": [_group("android", [_step()])]}
        )


def test_group_target_not_matching_the_one_declared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match the scenario's one declared"):
        Scenario.model_validate(
            {"name": "s", "targets": ["app"], "steps": [_group("web", [_step()])]}
        )


def test_group_target_rejected_when_scenario_declares_no_targets() -> None:
    with pytest.raises(ValidationError, match="declares no targets"):
        Scenario.model_validate({"name": "s", "steps": [_group("app", [_step()])]})


# --- a group cannot sit inside web:/app: — enforced by `_expand_target_groups`, since `Step`
# --- itself cannot see whether it is nested inside a web:/app: block --------------------------


def test_group_inside_web_rejected_at_expansion() -> None:
    with pytest.raises(ValueError, match="target group is not allowed nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "webview"},
                            "steps": [_group("web", [{"tap": {"id": "btn"}}])],
                        },
                    },
                ],
            }
        )


def test_group_inside_app_rejected_at_expansion() -> None:
    with pytest.raises(ValueError, match="target group is not allowed nested inside a web"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "app": {
                            "bundleId": "com.other.app",
                            "steps": [_group("app", [{"tap": {"id": "btn"}}])],
                        },
                    },
                ],
            }
        )


# --- expansion: a group-authored scenario matches a hand-flattened one -----------------------


def test_expansion_matches_a_hand_flattened_scenario() -> None:
    # A child's own modifiers (`name`, `capture`, `extract`, `from`) must survive stamping
    # unchanged — `_expand_steps` copies the whole step, not just its action fields.
    child_with_modifiers: dict[str, object] = {
        "tap": {"id": "x"},
        "name": "tap x",
        "capture": ["screenshot"],
        "extract": {"v": {"sel": {"id": "y"}}},
        "from": "tap the x button",
    }
    grouped = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group("app", [child_with_modifiers, {"wait": {"for": {"id": "y"}, "timeout": 5}}]),
                _group("web", [{"tap": {"id": "z"}}]),
            ],
        }
    )
    flat = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                {"target": "app", **child_with_modifiers},
                {"target": "app", "wait": {"for": {"id": "y"}, "timeout": 5}},
                {"target": "web", "tap": {"id": "z"}},
            ],
        }
    )
    assert grouped.steps == flat.steps
    assert grouped.steps[0].name == "tap x"
    assert grouped.steps[0].capture == ["screenshot"]
    assert grouped.steps[0].extract is not None
    assert grouped.steps[0].from_ == "tap the x button"


def test_no_group_remains_after_expansion() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [_group("app", [{"tap": {"id": "x"}}])],
        }
    )
    assert all(st.steps is None for st in s.steps)


def test_before_and_after_and_interrupts_are_expanded_the_same_way() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app"],
            "before": [_group("app", [{"tap": {"id": "b"}}])],
            "steps": [{"target": "app", "tap": {"id": "s"}}],
            "after": [{"on": "always", "steps": [_group("app", [{"tap": {"id": "a"}}])]}],
            "interrupts": [
                {
                    "condition": {"exists": {"id": "popup"}},
                    "steps": [_group("app", [{"tap": {"id": "i"}}])],
                },
            ],
        }
    )
    assert s.before[0].target == "app"
    assert s.before[0].tap is not None and s.before[0].tap.id == "b"
    assert s.after[0].steps[0].target == "app"
    assert s.after[0].steps[0].tap is not None and s.after[0].steps[0].tap.id == "a"
    assert s.interrupts[0].steps[0].target == "app"
    assert s.interrupts[0].steps[0].tap is not None and s.interrupts[0].steps[0].tap.id == "i"


def test_if_as_a_direct_child_inherits_the_groups_target() -> None:
    # Shallow inheritance: the `if` step itself is stamped, but its own `then`/`else` are a fresh
    # scope requiring their own explicit target (BE-0428's existing rule, unaffected by the group).
    # Both branches are exercised, since `_expand_nested` handles `then` and `else_` separately.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group(
                    "app",
                    [
                        {
                            "if": {
                                "condition": {"exists": {"id": "a"}},
                                "then": [{"target": "web", "tap": {"id": "x"}}],
                                "else": [{"target": "app", "tap": {"id": "y"}}],
                            }
                        },
                    ],
                ),
            ],
        }
    )
    if_step = s.steps[0]
    assert if_step.target == "app"
    assert if_step.if_ is not None
    assert if_step.if_.then[0].target == "web"
    assert if_step.if_.else_ is not None and if_step.if_.else_[0].target == "app"


def test_then_inside_a_group_child_still_requires_its_own_target() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        Scenario.model_validate(
            {
                "name": "s",
                "targets": ["app", "web"],
                "steps": [
                    _group(
                        "app",
                        [
                            {
                                "if": {
                                    "condition": {"exists": {"id": "a"}},
                                    "then": [{"tap": {"id": "x"}}],  # missing target
                                }
                            },
                        ],
                    ),
                ],
            }
        )


def test_for_each_as_a_direct_child_inherits_the_groups_target() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group(
                    "app",
                    [
                        {
                            "forEach": {
                                "sel": {"idMatches": "row.*"},
                                "as": "row",
                                "steps": [{"target": "app", "tap": {"id": "x"}}],
                            }
                        },
                    ],
                ),
            ],
        }
    )
    fe_step = s.steps[0]
    assert fe_step.target == "app"
    assert fe_step.for_each is not None and fe_step.for_each.steps[0].target == "app"


def test_web_as_a_direct_child_inherits_the_groups_target() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group(
                    "web",
                    [
                        {
                            "web": {
                                "within": {"id": "webview"},
                                "steps": [{"tap": {"id": "btn"}}],
                            }
                        },
                    ],
                ),
            ],
        }
    )
    web_step = s.steps[0]
    assert web_step.target == "web"
    assert web_step.web is not None and web_step.web.steps[0].target is None


def test_app_as_a_direct_child_inherits_the_groups_target() -> None:
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group(
                    "app",
                    [
                        {
                            "app": {
                                "bundleId": "com.other.app",
                                "steps": [{"tap": {"id": "btn"}}],
                            }
                        },
                    ],
                ),
            ],
        }
    )
    app_step = s.steps[0]
    assert app_step.target == "app"
    assert app_step.app is not None and app_step.app.steps[0].target is None


def test_group_nested_inside_for_each_body_is_its_own_fresh_scope() -> None:
    # The omission a group requires is shallow, not recursive: an inner group inside a forEach's
    # own body sets its own target freely, independent of the outer group's.
    s = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [
                _group(
                    "app",
                    [
                        {
                            "forEach": {
                                "sel": {"idMatches": "row.*"},
                                "as": "row",
                                "steps": [_group("web", [{"tap": {"id": "x"}}])],
                            }
                        },
                    ],
                ),
            ],
        }
    )
    fe_step = s.steps[0]
    assert fe_step.target == "app"
    assert fe_step.for_each is not None and fe_step.for_each.steps[0].target == "web"


# --- re-expansion survives expand_components / apply_setups / hook-folding --------------------


LOGIN_GROUP = Component(
    params=["user"],
    steps=[
        Step.model_validate(
            {
                "target": "app",
                "steps": [
                    {"type": {"into": {"id": "auth.user"}, "text": "${params.user}"}},
                    {"tap": {"id": "auth.submit"}},
                ],
            }
        ),
    ],
)


def test_expand_components_expands_a_group_the_component_carries() -> None:
    # The one-target path, matching `test_use_expansion_re_checks_target_requirements`; the
    # two-target routing of `use:` lives in `test_multi_target_use.py` (BE-0446).
    scns = load_scenarios(
        """
- name: s
  targets: [app]
  steps:
    - use: { component: login.yaml, with: { user: alice } }
"""
    )
    expand_components(scns, lambda ref: LOGIN_GROUP)
    steps = scns[0].steps
    assert [(st.target, st.type.text if st.type else _tap_id(st)) for st in steps] == [
        ("app", "alice"),
        ("app", "auth.submit"),
    ]


def test_apply_setups_expands_a_group_the_prelude_carries() -> None:
    scns = load_scenarios(
        """
- name: s
  targets: [app, web]
  preconditions: { setup: login }
  steps:
    - target: app
      tap: { id: own }
"""
    )
    prelude = [Step.model_validate(_group("web", [{"tap": {"id": "setup1"}}]))]
    apply_setups(scns, None, lambda ref: prelude)
    assert [(st.target, _tap_id(st)) for st in scns[0].steps] == [
        ("web", "setup1"),
        ("app", "own"),
    ]


_GROUPED_HOOK_CONFIG = """
targets:
  app:
    bundleId: com.example.app
    before:
      - target: app
        steps:
          - tap: { id: a1 }
          - tap: { id: a2 }
    after:
      - on: always
        steps:
          - target: app
            steps:
              - tap: { id: a3 }
  web:
    baseUrl: http://localhost:1/
"""


def test_hook_folding_expands_a_group_a_config_level_hook_carries() -> None:
    cfg = load_config(_GROUPED_HOOK_CONFIG)
    effs = {"app": resolve(cfg, "app"), "web": resolve(cfg, "web")}
    scenario = Scenario.model_validate(
        {
            "name": "s",
            "targets": ["app", "web"],
            "steps": [{"target": "app", "tap": {"id": "x"}}],
        }
    )
    merged = with_lifecycle_phases(effs["app"], [scenario], effs)[0]
    assert [(s.target, _tap_id(s)) for s in merged.before] == [("app", "a1"), ("app", "a2")]
    after_steps = merged.after[0].steps
    assert [(s.target, _tap_id(s)) for s in after_steps] == [("app", "a3")]
