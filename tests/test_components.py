"""Tests for parameterized shared steps (Component + expand_components)."""

from __future__ import annotations

import pytest

from bajutsu.scenario import (
    Component,
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


def _resolver(table: dict[str, Component]):
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


def test_nested_components_expand() -> None:
    table = {
        "inner.yaml": load_component(
            "params: [x]\nsteps:\n  - tap: { id: \"${params.x}\" }\n"
        ),
        "outer.yaml": load_component(
            "params: [y]\nsteps:\n  - use: { component: inner.yaml, with: { x: \"${params.y}\" } }\n"
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
    with pytest.raises(ValueError, match="不足"):
        expand_components(scns, _resolver({"login.yaml": LOGIN}))


def test_unknown_param_raises() -> None:
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: login.yaml, with: { user: a, pass: b, extra: c } }\n"
    )
    with pytest.raises(ValueError, match="未知"):
        expand_components(scns, _resolver({"login.yaml": LOGIN}))


def test_undeclared_param_token_raises() -> None:
    bad = load_component(
        "params: [a]\nsteps:\n  - tap: { id: \"${params.b}\" }\n"  # references undeclared b
    )
    scns = load_scenarios(
        "- name: s\n  steps:\n    - use: { component: bad.yaml, with: { a: x } }\n"
    )
    with pytest.raises(ValueError, match="未宣言"):
        expand_components(scns, _resolver({"bad.yaml": bad}))


def test_cycle_raises() -> None:
    table = {
        "a.yaml": load_component("steps:\n  - use: { component: b.yaml }\n"),
        "b.yaml": load_component("steps:\n  - use: { component: a.yaml }\n"),
    }
    scns = load_scenarios("- name: s\n  steps:\n    - use: { component: a.yaml }\n")
    with pytest.raises(ValueError, match="循環"):
        expand_components(scns, _resolver(table))
