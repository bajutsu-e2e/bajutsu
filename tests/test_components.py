"""Tests for parameterized shared steps (Component + expand_components)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.common.scenario import (
    Component,
    Step,
    apply_setups,
    expand_components,
    load_component,
    load_scenarios,
)

LOGIN = load_component(
    """
params: [user, pass]
steps:
  - type: { into: { id: auth.user }, text: "${params.user}" }
  - type: { into: { id: auth.pass }, text: "${params.pass}" }
  - tap: { id: auth.submit }
"""
)

LOGIN_TARGETED_ELSEWHERE = load_component(
    """
params: [user, pass]
steps:
  - target: other
    type: { into: { id: auth.user }, text: "${params.user}" }
"""
)


def _resolver(table: dict[str, Component]) -> Callable[[str], Component]:
    return lambda ref: table[ref]


def test_use_expands_and_substitutes_params() -> None:
    scns = load_scenarios(
        """
- name: s
  steps:
    - use: { component: login.yaml, with: { user: alice, pass: hunter2 } }
    - tap: { id: home.tab }
"""
    )
    expand_components(scns, _resolver({"login.yaml": LOGIN}))
    steps = scns[0].steps
    # 3 component steps (params substituted) + the scenario's own tap.
    assert len(steps) == 4
    assert steps[0].type is not None and steps[0].type.text == "alice"
    assert steps[1].type is not None and steps[1].type.text == "hunter2"
    assert steps[2].tap is not None and steps[2].tap.id == "auth.submit"
    assert steps[3].tap is not None and steps[3].tap.id == "home.tab"
    # No `use` steps remain after expansion.
    assert all(s.use is None for s in steps)


def test_use_expansion_re_checks_target_requirements() -> None:
    # BE-0428: at load time, the scenario's own `steps` holds only the `use` step, so the initial
    # validator never sees the component's own steps. Once `expand_components` splices them in,
    # the re-check must catch what the load-time pass could not: here, a component step whose own
    # `target` doesn't match the scenario's one declared target. (A `use:` step itself is refused
    # outright once a scenario declares two or more targets — see `test_target_routing.py` — so
    # this case, a component's own step disagreeing with the scenario, only arises for one.)
    scns = load_scenarios(
        """
- name: s
  targets: [app]
  steps:
    - use: { component: login.yaml, with: { user: alice, pass: hunter2 } }
"""
    )
    with pytest.raises(ValueError, match="does not match"):
        expand_components(scns, _resolver({"login.yaml": LOGIN_TARGETED_ELSEWHERE}))


EXTRACT_DUP_PARAMS = load_component(
    """
params: [name1, name2]
steps:
  - http:
      url: "https://api.test/data"
      extractBody:
        - { var: "${params.name1}", path: "a" }
        - { var: "${params.name2}", path: "b" }
"""
)


def test_use_expansion_re_checks_extract_body_var_collision() -> None:
    # BE-0440: the component's two entries use distinct `${params.*}` tokens, so the raw
    # component parses cleanly on its own — the collision exists only once `expand_components`
    # substitutes both to the same caller-supplied name, which the load-time `Scenario` validator
    # never sees (it runs before `use` is expanded).
    scns = load_scenarios(
        """
- name: s
  steps:
    - use: { component: extract.yaml, with: { name1: token, name2: token } }
"""
    )
    with pytest.raises(ValueError, match="token"):
        expand_components(scns, _resolver({"extract.yaml": EXTRACT_DUP_PARAMS}))


def test_apply_setups_re_checks_extract_body_var_collision() -> None:
    # BE-0440: a `resolve` that hands back bare `Step`s never validated through a full
    # `Scenario.model_validate` — unlike the CLI's own `_setup_steps`, which always loads a
    # prelude as a scenario file first — is exactly what `apply_setups`'s own re-check defends
    # against: its single `http` step already carries the collision, so this stands in for a
    # prelude source that skipped validation.
    steps = [
        Step.model_validate(
            {
                "http": {
                    "url": "https://api.test/data",
                    "saveBody": "token",
                    "extractBody": [{"var": "token", "path": "a"}],
                }
            }
        )
    ]
    scns = load_scenarios("- name: s\n  steps:\n    - tap: { id: home.tab }\n")
    with pytest.raises(ValueError, match="token"):
        apply_setups(scns, "setup.yaml", lambda ref: steps)


def test_nested_components_expand() -> None:
    table = {
        "inner.yaml": load_component('params: [x]\nsteps:\n  - tap: { id: "${params.x}" }\n'),
        "outer.yaml": load_component(
            'params: [y]\nsteps:\n  - use: { component: inner.yaml, with: { x: "${params.y}" } }\n'
        ),
    }
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: outer.yaml, with: { y: target } }\n"
    )
    expand_components(scns, _resolver(table))
    steps = scns[0].steps
    assert len(steps) == 1
    assert steps[0].tap is not None and steps[0].tap.id == "target"


def test_missing_param_raises() -> None:
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: login.yaml, with: { user: alice } }\n"
    )
    with pytest.raises(ValueError, match="missing required params"):
        expand_components(scns, _resolver({"login.yaml": LOGIN}))


def test_unknown_param_raises() -> None:
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: login.yaml, with: { user: a, pass: b, extra: c } }\n"
    )
    with pytest.raises(ValueError, match="unknown params"):
        expand_components(scns, _resolver({"login.yaml": LOGIN}))


def test_undeclared_param_token_raises() -> None:
    bad = load_component(
        'params: [a]\nsteps:\n  - tap: { id: "${params.b}" }\n'  # references undeclared b
    )
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: bad.yaml, with: { a: x } }\n"
    )
    with pytest.raises(ValueError, match="undeclared params"):
        expand_components(scns, _resolver({"bad.yaml": bad}))


def test_cycle_raises() -> None:
    table = {
        "a.yaml": load_component("steps:\n  - use: { component: b.yaml }\n"),
        "b.yaml": load_component("steps:\n  - use: { component: a.yaml }\n"),
    }
    scns = load_scenarios("- name: s\n  steps:\n    - use: { component: a.yaml }\n")
    with pytest.raises(ValueError, match="cycle detected"):
        expand_components(scns, _resolver(table))


def test_nesting_deeper_than_max_depth_raises() -> None:
    # A runaway chain with no cycle in it: the depth cap, not the cycle check, is what stops it.
    table = {
        "a.yaml": load_component("steps:\n  - use: { component: b.yaml }\n"),
        "b.yaml": load_component("steps:\n  - tap: { id: deep }\n"),
    }
    scns = load_scenarios("- name: s\n  steps:\n    - use: { component: a.yaml }\n")
    with pytest.raises(ValueError, match="nesting too deep"):
        expand_components(scns, _resolver(table), max_depth=1)


def test_use_expands_inside_an_interrupt_handler() -> None:
    # An `interrupts` handler's recovery steps run through the same step loop, so a `use` there
    # must be expanded like any other — an unexpanded one reaches the loop with no action at all.
    scns = load_scenarios(
        """
- name: s
  interrupts:
    - condition: { exists: { id: onboarding.title } }
      steps:
        - use: { component: login.yaml, with: { user: alice, pass: hunter2 } }
        - tap: { id: onboarding.skip }
  steps:
    - tap: { id: home.tab }
"""
    )
    expand_components(scns, _resolver({"login.yaml": LOGIN}))
    steps = scns[0].interrupts[0].steps
    assert len(steps) == 4  # 3 component steps + the handler's own tap
    assert steps[0].type is not None and steps[0].type.text == "alice"
    assert steps[1].type is not None and steps[1].type.text == "hunter2"
    assert steps[2].tap is not None and steps[2].tap.id == "auth.submit"
    assert steps[3].tap is not None and steps[3].tap.id == "onboarding.skip"
    assert all(s.use is None for s in steps)


def test_component_errors_surface_from_an_interrupt_handler() -> None:
    scns = load_scenarios(
        """
- name: s
  interrupts:
    - condition: { exists: { id: x } }
      steps:
        - use: { component: login.yaml, with: { user: alice } }
  steps:
    - tap: { id: home.tab }
"""
    )
    with pytest.raises(ValueError, match="missing required params"):
        expand_components(scns, _resolver({"login.yaml": LOGIN}))
