"""Tests for `use:` / `group:` routing in a multi-target scenario (BE-0446).

Covers both sources of an expanded step's target — a caller naming `target` (stamped onto every
step it produces, down through `if` / `forEach` bodies and nested calls, stopping at `web:` /
`app:`) and a caller omitting it (each step resolves as a hand-written one would) — the
equal-accepted / different-refused conflict rule, `expand()`'s recursion into control-flow bodies
under any target count, and the load-time failure a broken `use:` in an untaken branch now raises.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.common.scenario import Component, Scenario, Step, expand_components, load_component

TWO = ["app", "web"]

SIGN_IN = load_component(
    """
params: [email]
steps:
  - type: { into: { id: auth.email }, text: "${params.email}" }
  - tap: { id: auth.submit }
    name: submit
"""
)

SIGN_IN_ON_WEB = load_component(
    """
steps:
  - target: web
    tap: { id: auth.submit }
    name: submit
"""
)

CROSS_TARGET = load_component(
    """
steps:
  - target: app
    tap: { id: like }
  - target: web
    wait: { for: { id: liked }, timeout: 5 }
"""
)

BRANCHY = load_component(
    """
steps:
  - if:
      condition: { exists: { id: banner } }
      then: [{ tap: { id: banner.close } }]
      else: [{ tap: { id: noop } }]
  - forEach:
      sel: { idMatches: ["row.*"] }
      as: x
      steps: [{ tap: { id: row } }]
  - web:
      within: { id: wv }
      steps: [{ tap: { id: inner } }]
"""
)

OUTER = load_component(
    """
steps:
  - use: { component: sign-in, with: { email: a@b.com } }
  - tap: { id: after }
"""
)

OUTER_NAMING_APP = load_component(
    """
steps:
  - use: { component: sign-in, with: { email: a@b.com } }
    target: app
"""
)

PARAMETERIZED = load_component(
    """
params: [device]
steps:
  - target: "${params.device}"
    tap: { id: go }
"""
)

GROUP_IN_BODY = load_component(
    """
steps:
  - if:
      condition: { exists: { id: a } }
      then:
        - group: { name: inner, steps: [{ tap: { id: a } }] }
"""
)

TARGET_GROUP_WITH_USE = load_component(
    """
steps:
  - target: app
    steps:
      - use: { component: sign-in-on-web }
"""
)

TARGET_GROUP_WITH_IF = load_component(
    """
steps:
  - target: web
    steps:
      - if:
          condition: { exists: { id: a } }
          then: [{ tap: { id: b } }]
"""
)

SELF_IN_BODY = load_component(
    """
steps:
  - if:
      condition: { exists: { id: a } }
      then: [{ use: { component: self-in-body } }]
"""
)

GROUP_NAMING_WEB = load_component(
    """
steps:
  - target: web
    group: { name: g, steps: [{ tap: { id: a } }] }
"""
)

TARGET_GROUP_IN_WEB_IF = load_component(
    """
steps:
  - web:
      within: { id: wv }
      steps:
        - if:
            condition: { exists: { id: a } }
            then:
              - target: app
                steps: [{ tap: { id: b } }]
"""
)

TABLE: dict[str, Component] = {
    "sign-in": SIGN_IN,
    "sign-in-on-web": SIGN_IN_ON_WEB,
    "cross-target": CROSS_TARGET,
    "branchy": BRANCHY,
    "outer": OUTER,
    "outer-naming-app": OUTER_NAMING_APP,
    "parameterized": PARAMETERIZED,
    "group-in-body": GROUP_IN_BODY,
    "target-group-with-use": TARGET_GROUP_WITH_USE,
    "target-group-with-if": TARGET_GROUP_WITH_IF,
    "self-in-body": SELF_IN_BODY,
    "group-naming-web": GROUP_NAMING_WEB,
    "target-group-in-web-if": TARGET_GROUP_IN_WEB_IF,
}


def _resolve(ref: str) -> Component:
    if ref not in TABLE:
        raise ValueError(f"unknown component {ref!r}")
    return TABLE[ref]


def _expanded(**fields: object) -> Scenario:
    s = Scenario.model_validate({"name": "s", **fields})
    expand_components([s], _resolve)
    return s


def _use(component: str, **extra: object) -> dict[str, object]:
    with_ = {"email": "a@b.com"} if component == "sign-in" else {}
    return {"use": {"component": component, "with": with_}, **extra}


def _targets(steps: list[Step]) -> list[str | None]:
    return [st.target for st in steps]


# --- the caller names `target` ------------------------------------------------------------------


def test_caller_target_is_stamped_onto_every_expanded_step() -> None:
    s = _expanded(targets=TWO, steps=[_use("sign-in", target="web")])
    assert _targets(s.steps) == ["web", "web"]
    assert [st.resolved_target for st in s.steps] == ["web", "web"]


def test_an_equal_target_inside_the_component_is_accepted() -> None:
    s = _expanded(targets=TWO, steps=[_use("sign-in-on-web", target="web")])
    assert _targets(s.steps) == ["web"]


def test_a_different_target_inside_the_component_is_refused_with_the_chain() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"^use: sign-in-on-web > step 'submit': target 'web' conflicts with the caller's "
            r"target 'app'$"
        ),
    ):
        _expanded(targets=TWO, steps=[_use("sign-in-on-web", target="app")])


def test_a_single_target_scenario_may_name_its_one_target_on_use() -> None:
    s = _expanded(targets=["app"], steps=[_use("sign-in", target="app")])
    assert _targets(s.steps) == ["app", "app"]


def test_use_target_with_no_declared_targets_is_refused() -> None:
    with pytest.raises(ValueError, match="declares no targets"):
        _expanded(steps=[_use("sign-in", target="app")])


# --- the caller omits `target` ------------------------------------------------------------------


def test_a_cross_target_component_keeps_its_own_routing() -> None:
    s = _expanded(targets=TWO, steps=[_use("cross-target")])
    assert _targets(s.steps) == ["app", "web"]


def test_an_omitted_target_resolves_through_the_primary() -> None:
    s = _expanded(targets=TWO, primaryTarget="app", steps=[_use("sign-in")])
    assert _targets(s.steps) == [None, None]
    assert [st.resolved_target for st in s.steps] == ["app", "app"]


def test_an_omitted_target_with_no_primary_is_a_load_time_error() -> None:
    with pytest.raises(ValueError, match="target is required — the scenario declares 2 targets"):
        _expanded(targets=TWO, steps=[_use("sign-in")])


def test_a_parameterized_target_substitutes_before_the_check() -> None:
    s = _expanded(
        targets=TWO, steps=[{"use": {"component": "parameterized", "with": {"device": "web"}}}]
    )
    assert _targets(s.steps) == ["web"]


def test_a_parameterized_target_naming_an_undeclared_target_is_refused() -> None:
    with pytest.raises(ValueError, match="target 'ios' is not one of"):
        _expanded(
            targets=TWO, steps=[{"use": {"component": "parameterized", "with": {"device": "ios"}}}]
        )


# --- how far a caller's target reaches ----------------------------------------------------------


def test_the_stamp_descends_into_if_and_for_each_and_stops_at_web() -> None:
    s = _expanded(targets=TWO, steps=[_use("branchy", target="app")])
    if_step, for_each_step, web_step = s.steps
    assert if_step.target == "app" and if_step.if_ is not None
    assert _targets(if_step.if_.then) == ["app"]
    assert if_step.if_.else_ is not None and _targets(if_step.if_.else_) == ["app"]
    assert for_each_step.target == "app" and for_each_step.for_each is not None
    assert _targets(for_each_step.for_each.steps) == ["app"]
    # The `web:` wrapper itself is stamped; the steps inside it keep omitting `target`.
    assert web_step.target == "app" and web_step.web is not None
    assert _targets(web_step.web.steps) == [None]


def test_the_stamp_passes_through_a_nested_use() -> None:
    s = _expanded(targets=TWO, steps=[_use("outer", target="web")])
    assert _targets(s.steps) == ["web", "web", "web"]


def test_a_nested_use_naming_a_different_target_is_refused_with_the_chain() -> None:
    with pytest.raises(
        ValueError,
        match=r"^use: outer-naming-app > use: sign-in: target 'app' conflicts with the caller's "
        r"target 'web'$",
    ):
        _expanded(targets=TWO, steps=[_use("outer-naming-app", target="web")])


def test_a_nested_use_naming_a_target_stamps_it_on_its_own() -> None:
    s = _expanded(targets=TWO, steps=[_use("outer-naming-app")])
    assert _targets(s.steps) == ["app", "app"]


def test_a_target_group_in_a_component_passes_its_target_to_a_nested_use() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"^use: target-group-with-use > target group 'app' > use: sign-in-on-web > step 'submit': target 'web' conflicts "
            r"with the caller's target 'app'$"
        ),
    ):
        _expanded(targets=TWO, steps=[_use("target-group-with-use")])


def test_a_target_group_in_a_component_leaves_its_bodies_a_fresh_scope() -> None:
    # BE-0437: the group stamps its direct children alone, so the `if` body resolves through the
    # primary rather than picking up the group's target.
    s = _expanded(targets=TWO, primaryTarget="app", steps=[_use("target-group-with-if")])
    (if_step,) = s.steps
    assert if_step.target == "web" and if_step.if_ is not None
    (inner,) = if_step.if_.then
    assert inner.target is None and inner.resolved_target == "app"


def test_a_group_naming_target_stamps_through_its_bodies() -> None:
    # Unlike a target group, a `group:` caller target reaches every depth (BE-0446).
    s = _expanded(
        targets=TWO,
        primaryTarget="app",
        steps=[
            {
                "target": "web",
                "group": {"name": "g", "steps": [_if([{"tap": {"id": "b"}}])]},
            }
        ],
    )
    (if_step,) = s.steps
    assert if_step.if_ is not None and _targets(if_step.if_.then) == ["web"]


def test_a_caller_target_conflicting_with_a_group_in_the_component_is_refused() -> None:
    with pytest.raises(
        ValueError,
        match=r"^use: group-naming-web > group: 'g': target 'web' conflicts with the caller's "
        r"target 'app'$",
    ):
        _expanded(targets=TWO, steps=[_use("group-naming-web", target="app")])


def test_a_cycle_through_a_body_is_detected() -> None:
    with pytest.raises(ValueError, match="component cycle detected: self-in-body -> self-in-body"):
        _expanded(steps=[_use("self-in-body")])


def test_a_target_group_under_an_if_inside_web_is_refused_as_a_target_group() -> None:
    with pytest.raises(
        ValueError, match="a target group is not allowed nested inside a web: or app:"
    ):
        _expanded(targets=TWO, primaryTarget="app", steps=[_use("target-group-in-web-if")])


def test_a_scenario_level_target_group_stamps_a_use_inside_it() -> None:
    s = _expanded(targets=TWO, steps=[{"target": "web", "steps": [_use("sign-in")]}])
    assert _targets(s.steps) == ["web", "web"]


# --- group: follows the same rule ---------------------------------------------------------------


def test_group_naming_target_under_two_targets_with_no_primary() -> None:
    s = _expanded(
        targets=TWO,
        steps=[
            {
                "target": "web",
                "group": {"name": "sign in", "steps": [{"tap": {"id": "a"}}, _use("sign-in")]},
            }
        ],
    )
    assert _targets(s.steps) == ["web", "web", "web"]
    assert {st.report_group for st in s.steps} == {"sign in"}


def test_group_with_a_conflicting_child_is_refused_at_load_time() -> None:
    with pytest.raises(
        ValueError, match="target 'app' conflicts with the enclosing group's target 'web'"
    ):
        _expanded(
            targets=TWO,
            steps=[
                {
                    "target": "web",
                    "group": {"name": "g", "steps": [{"tap": {"id": "a"}, "target": "app"}]},
                }
            ],
        )


def test_group_with_a_conflicting_component_step_is_refused_with_the_chain() -> None:
    with pytest.raises(
        ValueError,
        match=r"^group: 'g' > use: sign-in-on-web > step 'submit': target 'web' conflicts with "
        r"the caller's target 'app'$",
    ):
        _expanded(
            targets=TWO,
            steps=[{"target": "app", "group": {"name": "g", "steps": [_use("sign-in-on-web")]}}],
        )


def test_group_omitting_target_leaves_each_child_to_resolve() -> None:
    s = _expanded(
        targets=TWO,
        primaryTarget="app",
        steps=[
            {
                "group": {
                    "name": "g",
                    "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}, "target": "web"}],
                }
            }
        ],
    )
    assert [st.resolved_target for st in s.steps] == ["app", "web"]


def test_a_group_carried_into_a_body_by_a_component_is_refused() -> None:
    with pytest.raises(ValueError, match="group 'inner' must not nest inside if"):
        _expanded(targets=TWO, primaryTarget="app", steps=[_use("group-in-body")])


# --- recursion into control-flow bodies, at any target count -------------------------------------


def _if(then: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"if": {"condition": {"exists": {"id": "x"}}, "then": then}, **extra}


def _else(else_: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {
        "if": {"condition": {"exists": {"id": "x"}}, "then": [{"tap": {"id": "t"}}], "else": else_},
        **extra,
    }


def _for_each(steps: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"forEach": {"sel": {"idMatches": ["row.*"]}, "as": "x", "steps": steps}, **extra}


def _web(steps: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"web": {"within": {"id": "wv"}, "steps": steps}, **extra}


def _app(steps: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"app": {"bundleId": "com.example", "steps": steps}, **extra}


def _body(step: Step) -> list[Step]:
    if step.if_ is not None:
        return step.if_.else_ if step.if_.else_ is not None else step.if_.then
    if step.for_each is not None:
        return step.for_each.steps
    if step.web is not None:
        return step.web.steps
    assert step.app is not None
    return step.app.steps


_BODIES: list[Callable[..., dict[str, object]]] = [_if, _else, _for_each, _web, _app]


@pytest.mark.parametrize("wrap", _BODIES)
def test_use_expands_inside_every_body_with_no_targets(
    wrap: Callable[..., dict[str, object]],
) -> None:
    s = _expanded(steps=[wrap([_use("sign-in")])])
    body = _body(s.steps[0])
    assert all(st.use is None for st in body)
    assert len(body) == 2


@pytest.mark.parametrize("wrap", _BODIES)
def test_use_expands_inside_every_body_under_two_targets(
    wrap: Callable[..., dict[str, object]],
) -> None:
    s = _expanded(targets=TWO, primaryTarget="app", steps=[wrap([_use("sign-in")], target="web")])
    body = _body(s.steps[0])
    assert all(st.use is None for st in body)
    assert len(body) == 2


def test_a_use_in_a_web_block_whose_component_names_a_target_is_refused() -> None:
    with pytest.raises(ValueError, match="nested inside a web: or app: block"):
        _expanded(targets=TWO, steps=[_web([_use("sign-in-on-web")], target="app")])


def test_a_broken_use_in_an_untaken_branch_now_fails_at_load_time() -> None:
    # The branch's condition never needs evaluating: expansion reaches every body at load time.
    with pytest.raises(ValueError, match="unknown component 'missing'"):
        _expanded(steps=[_if([{"use": {"component": "missing", "with": {}}}])])
    with pytest.raises(ValueError, match="missing required params"):
        _expanded(steps=[_for_each([{"use": {"component": "sign-in", "with": {}}}])])


# --- interrupt recovery steps -------------------------------------------------------------------


def _with_recovery(recovery: dict[str, object], **fields: object) -> Scenario:
    return _expanded(
        targets=TWO,
        steps=[{"tap": {"id": "a"}, "target": "app"}],
        interrupts=[{"condition": {"exists": {"id": "popup"}}, "steps": [recovery]}],
        **fields,
    )


def test_a_recovery_use_naming_target_stamps_it() -> None:
    s = _with_recovery(_use("sign-in", target="web"))
    assert _targets(s.interrupts[0].steps) == ["web", "web"]


def test_a_recovery_use_omitting_target_stays_on_the_guard_runner_even_with_a_primary() -> None:
    s = _with_recovery(_use("sign-in"), primaryTarget="app")
    assert [st.resolved_target for st in s.interrupts[0].steps] == [None, None]


def test_lifecycle_before_use_follows_the_main_steps_rule() -> None:
    s = _expanded(
        targets=TWO,
        before=[_use("sign-in", target="web")],
        steps=[{"tap": {"id": "a"}, "target": "app"}],
    )
    assert _targets(s.before) == ["web", "web"]
