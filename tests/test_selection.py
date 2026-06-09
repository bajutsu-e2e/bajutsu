"""Tests for tag-based scenario selection (Scenario.tags + select_scenarios)."""

from __future__ import annotations

from bajutsu.scenario import (
    Scenario,
    Selector,
    Step,
    load_scenarios,
    select_scenarios,
)


def _scn(name: str, tags: list[str]) -> Scenario:
    return Scenario(name=name, tags=tags, steps=[Step(tap=Selector(id="x"))])


def test_tags_default_empty() -> None:
    s = Scenario(name="s", steps=[Step(tap=Selector(id="x"))])
    assert s.tags == []


def test_yaml_accepts_tags() -> None:
    s = load_scenarios(
        """
- name: s
  tags: [smoke, auth]
  steps:
    - tap: { id: x }
"""
    )[0]
    assert s.tags == ["smoke", "auth"]


def test_include_only() -> None:
    scns = [_scn("a", ["smoke"]), _scn("b", ["slow"]), _scn("c", ["smoke", "auth"])]
    assert [s.name for s in select_scenarios(scns, ["smoke"], [])] == ["a", "c"]


def test_exclude_only() -> None:
    scns = [_scn("a", ["smoke"]), _scn("b", ["slow"])]
    assert [s.name for s in select_scenarios(scns, [], ["slow"])] == ["a"]


def test_empty_filters_return_all() -> None:
    scns = [_scn("a", ["smoke"]), _scn("b", [])]
    assert select_scenarios(scns, [], []) == scns


def test_exclude_wins_over_include() -> None:
    scns = [_scn("a", ["smoke", "slow"])]
    assert select_scenarios(scns, ["smoke"], ["slow"]) == []


def test_preserves_order_and_does_not_mutate() -> None:
    scns = [_scn("a", ["x"]), _scn("b", ["x"])]
    out = select_scenarios(scns, ["x"], [])
    assert [s.name for s in out] == ["a", "b"]
    assert len(scns) == 2
