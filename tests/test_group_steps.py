"""Tests for the `group` step: compile-time expansion and `report_group` / `report_group_id`."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.common.orchestrator.actions._registry import _RUNTIME_ACTIONS
from bajutsu.common.scenario import Component, expand_components, load_component, load_scenarios
from bajutsu.common.scenario.serialize import redact_totp_secrets


def _resolver(table: dict[str, Component]) -> Callable[[str], Component]:
    return lambda ref: table[ref]


def test_group_expands_and_tags_its_steps() -> None:
    scns = load_scenarios(
        """
- name: s
  steps:
    - group:
        name: login
        steps:
          - tap: { id: auth.open }
          - tap: { id: auth.submit }
    - tap: { id: home.tab }
"""
    )
    expand_components(scns, _resolver({}))
    steps = scns[0].steps
    assert len(steps) == 3
    assert all(s.group is None for s in steps)
    assert steps[0].report_group == "login"
    assert steps[1].report_group == "login"
    assert steps[0].report_group_id == steps[1].report_group_id
    assert steps[2].report_group is None
    assert steps[2].report_group_id is None


def test_two_group_invocations_get_different_ids() -> None:
    scns = load_scenarios(
        """
- name: s
  steps:
    - group:
        name: retry
        steps:
          - tap: { id: a }
    - group:
        name: retry
        steps:
          - tap: { id: b }
"""
    )
    expand_components(scns, _resolver({}))
    first, second = scns[0].steps
    assert first.report_group == second.report_group == "retry"
    assert first.report_group_id != second.report_group_id


def test_use_called_inside_a_group_tags_the_components_own_steps() -> None:
    login = load_component(
        'params: [user]\nsteps:\n  - type: { into: { id: auth.user }, text: "${params.user}" }\n'
        "  - tap: { id: auth.submit }\n"
    )
    scns = load_scenarios(
        """
- name: s
  steps:
    - group:
        name: login
        steps:
          - use: { component: login.yaml, with: { user: alice } }
"""
    )
    expand_components(scns, _resolver({"login.yaml": login}))
    steps = scns[0].steps
    assert len(steps) == 2
    assert all(s.report_group == "login" for s in steps)
    assert steps[0].report_group_id == steps[1].report_group_id


def test_a_group_reached_through_use_inside_a_group_raises() -> None:
    inner = load_component(
        "params: []\nsteps:\n  - group: { name: inner, steps: [{tap: {id: x}}] }\n"
    )
    scns = load_scenarios(
        """
- name: s
  steps:
    - group:
        name: outer
        steps:
          - use: { component: inner.yaml }
"""
    )
    with pytest.raises(ValueError, match="nested inside group 'outer'"):
        expand_components(scns, _resolver({"inner.yaml": inner}))


def test_group_expands_in_before_and_after_and_interrupts() -> None:
    scns = load_scenarios(
        """
- name: s
  before:
    - group: { name: setup, steps: [{tap: {id: a}}] }
  after:
    - on: always
      steps:
        - group: { name: teardown, steps: [{tap: {id: b}}] }
  interrupts:
    - condition: { exists: { id: dialog } }
      steps:
        - group: { name: dismiss, steps: [{tap: {id: dialog.close}}] }
  steps:
    - tap: { id: c }
"""
    )
    expand_components(scns, _resolver({}))
    scenario = scns[0]
    assert scenario.before[0].report_group == "setup"
    assert scenario.after[0].steps[0].report_group == "teardown"
    assert scenario.interrupts[0].steps[0].report_group == "dismiss"


def test_a_group_id_stays_unique_across_separate_expand_components_calls() -> None:
    # A `setup` prelude expands through its own, separate `expand_components` call
    # (`run/cli.py`'s `_setup_steps`) before the scenario's own call runs. The id must not repeat
    # across the two, or `_fold_groups` could merge unrelated groups that land adjacent.
    prelude = load_scenarios(
        "- name: prelude\n  steps:\n    - group: { name: setup, steps: [{tap: {id: a}}] }\n"
    )
    main = load_scenarios(
        "- name: main\n  steps:\n    - group: { name: setup, steps: [{tap: {id: b}}] }\n"
    )
    expand_components(prelude, _resolver({}))
    expand_components(main, _resolver({}))
    assert prelude[0].steps[0].report_group_id != main[0].steps[0].report_group_id


def test_group_is_excluded_from_the_runtime_action_list() -> None:
    # `group` disappears before `run`, exactly like `use` — the orchestrator must never try to
    # dispatch on it, whether it was expanded correctly or (a bug) left behind.
    assert "group" not in _RUNTIME_ACTIONS
    assert "use" not in _RUNTIME_ACTIONS
    assert "tap" in _RUNTIME_ACTIONS


def test_redact_totp_secrets_round_trips_an_expanded_group() -> None:
    # `redact_totp_secrets` dumps the scenario (`by_alias`, `exclude_none`, `exclude_defaults`) and
    # re-validates that same dump. A `group:` invocation tags its flattened steps via
    # `model_copy(update=...)`, which makes `report_group` / `report_group_id` *set*, non-default
    # fields that `exclude_defaults` therefore keeps — so this must survive the round trip. It runs
    # on every executed scenario (`runner/pool.py`, `runner/pipeline.py`) before the run's evidence
    # snapshot is written.
    scns = load_scenarios(
        """
- name: s
  steps:
    - group:
        name: login
        steps:
          - tap: { id: auth.open }
"""
    )
    expand_components(scns, _resolver({}))
    redacted = redact_totp_secrets(scns[0])
    assert redacted.steps[0].report_group == "login"
    assert redacted.steps[0].report_group_id == scns[0].steps[0].report_group_id
