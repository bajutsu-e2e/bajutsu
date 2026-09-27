"""Tests for `Scenario.primaryTarget` and `Step.resolved_target` (BE-0436).

Covers the `primaryTarget` constraint (unset, or equal to `targets[0]`), the omitted-`target`
escape it opens under two or more declared targets on every step shape and on a top-level `expect`
entry, the zero/one-target shapes it leaves unchanged, the `web:` / `app:` / `use:` rules it leaves
in force, `interrupts`' own independence from it (BE-0438), the round-trip guarantee (the resolved
name never reaches `model_dump()`), and `apply_setups` cloning its cached steps so scenarios
sharing a setup resolve independently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import Scenario, Step, apply_setups, scenario_dict

_TWO = {"targets": ["app", "web"], "primaryTarget": "app"}


def _step(**overrides: object) -> dict[str, object]:
    return {"tap": {"id": "a"}, **overrides}


def _scenario(**fields: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **fields})


# --- declaring primaryTarget ------------------------------------------------------------------


def test_primary_target_defaults_to_unset() -> None:
    s = _scenario(targets=["app", "web"], steps=[_step(target="app")])
    assert s.primary_target is None
    assert s.steps[0].resolved_target == "app"


def test_primary_target_must_be_the_first_declared_target() -> None:
    with pytest.raises(
        ValidationError, match=r"primaryTarget 'web' must be the first entry of targets \('app'\)"
    ):
        _scenario(targets=["app", "web"], primaryTarget="web", steps=[_step()])


def test_primary_target_naming_an_undeclared_target_rejected() -> None:
    with pytest.raises(ValidationError, match="must be the first entry of targets"):
        _scenario(targets=["app", "web"], primaryTarget="other", steps=[_step()])


def test_primary_target_without_targets_rejected() -> None:
    with pytest.raises(ValidationError, match="primaryTarget is set but the scenario declares no"):
        _scenario(primaryTarget="app", steps=[_step()])


def test_unset_primary_target_keeps_target_required() -> None:
    with pytest.raises(ValidationError, match="target is required"):
        _scenario(targets=["app", "web"], steps=[_step()])


# --- an omitted target resolves to the primary, on every step shape --------------------------


def test_omitted_step_target_resolves_to_the_primary() -> None:
    s = _scenario(**_TWO, steps=[_step(), _step(target="web"), _step(target="app")])
    assert [st.resolved_target for st in s.steps] == ["app", "web", "app"]
    assert s.steps[0].target is None  # never written back onto the authored field


def test_before_and_after_steps_resolve_the_same_way() -> None:
    s = _scenario(
        **_TWO,
        before=[_step()],
        steps=[_step(target="web")],
        after=[{"on": "always", "steps": [_step()]}],
    )
    assert s.before[0].resolved_target == "app"
    assert s.after[0].steps[0].resolved_target == "app"


def test_nested_if_and_for_each_steps_resolve_to_the_primary_not_their_wrapper() -> None:
    # The resolution is flat: a nested step omitting `target` means the primary even when its
    # wrapper routes to another target (BE-0436 — no inheritance from the enclosing wrapper).
    s = _scenario(
        **_TWO,
        steps=[
            {
                "target": "web",
                "if": {
                    "condition": {"exists": {"id": "a"}},
                    "then": [_step()],
                    "else": [_step(target="web")],
                },
            },
            {"forEach": {"sel": {"idMatches": "row.*"}, "as": "row", "steps": [_step()]}},
        ],
    )
    if_step, for_each_step = s.steps
    assert if_step.resolved_target == "web"
    assert if_step.if_ is not None
    assert if_step.if_.then[0].resolved_target == "app"
    assert if_step.if_.else_ is not None
    assert if_step.if_.else_[0].resolved_target == "web"
    assert for_each_step.resolved_target == "app"
    assert for_each_step.for_each is not None
    assert for_each_step.for_each.steps[0].resolved_target == "app"


@pytest.mark.parametrize("block", ["web", "app"])
def test_web_and_app_wrappers_resolve_but_their_nested_steps_do_not(block: str) -> None:
    body: dict[str, object] = {"steps": [_step()]}
    body |= {"within": {"id": "webview"}} if block == "web" else {"bundleId": "com.example"}
    s = _scenario(**_TWO, steps=[{block: body}])
    wrapper = s.steps[0]
    assert wrapper.resolved_target == "app"
    nested = wrapper.web.steps[0] if wrapper.web is not None else wrapper.app.steps[0]  # type: ignore[union-attr]
    # A block's nested step always runs on the block's own device, so it resolves to nothing.
    assert nested.target is None
    assert nested.resolved_target is None


@pytest.mark.parametrize("block", ["web", "app"])
def test_web_and_app_nested_steps_still_reject_an_explicit_target(block: str) -> None:
    body: dict[str, object] = {"steps": [_step(target="app")]}
    body |= {"within": {"id": "webview"}} if block == "web" else {"bundleId": "com.example"}
    with pytest.raises(ValidationError, match="not allowed on a step nested inside a web"):
        _scenario(**_TWO, steps=[{block: body}])


def test_omitted_expect_target_is_legal() -> None:
    s = _scenario(
        **_TWO,
        steps=[_step()],
        expect=[{"value": {"sel": {"id": "a"}, "equals": "1"}}],
    )
    # `_evaluate_expect` groups an omitted entry under the run's primary; nothing is stamped here.
    assert s.expect[0].target is None


def test_omitted_expect_target_still_required_without_primary() -> None:
    with pytest.raises(ValidationError, match="expect entry: target is required"):
        _scenario(
            targets=["app", "web"],
            steps=[_step(target="app")],
            expect=[{"value": {"sel": {"id": "a"}, "equals": "1"}}],
        )


def test_explicit_undeclared_target_still_rejected() -> None:
    with pytest.raises(ValidationError, match="not one of the scenario's declared targets"):
        _scenario(**_TWO, steps=[_step(target="other")])


# --- zero / one declared targets: unchanged ---------------------------------------------------


def test_zero_targets_leaves_resolved_target_unset() -> None:
    s = _scenario(steps=[_step()])
    assert s.steps[0].resolved_target is None


@pytest.mark.parametrize("primary", [None, "app"])
def test_one_target_behaves_as_before_either_way(primary: str | None) -> None:
    fields: dict[str, object] = {"targets": ["app"], "steps": [_step(), _step(target="app")]}
    if primary is not None:
        fields["primaryTarget"] = primary
    s = _scenario(**fields)
    assert [st.resolved_target for st in s.steps] == [None, "app"]


def test_one_target_still_rejects_a_mismatched_step_target() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        _scenario(targets=["app"], primaryTarget="app", steps=[_step(target="web")])


# --- what stays refused under two or more targets ---------------------------------------------


def test_use_still_rejected_with_a_primary() -> None:
    with pytest.raises(ValidationError, match="use: is not yet supported"):
        _scenario(**_TWO, steps=[{"use": {"component": "login.yaml", "with": {}}}])


# --- interrupts: unaffected by primaryTarget (BE-0438) -----------------------------------------


def test_an_interrupts_entry_still_works_with_a_primary_set() -> None:
    # BE-0438 lifted the blanket refusal this module's own docstring once described; a scenario
    # declaring `primaryTarget` uses `interrupts` exactly as one without it does.
    s = _scenario(
        **_TWO,
        steps=[_step()],
        interrupts=[{"condition": {"exists": {"id": "popup"}}, "steps": [_step()]}],
    )
    assert s.interrupts[0].target is None


def test_a_recovery_step_omitting_target_never_resolves_through_the_primary() -> None:
    # An interrupts entry's own resolution (its `target`, or the runner whose guard fired) is
    # independent of `primaryTarget` — unlike an ordinary step, a recovery step omitting `target`
    # never picks up the scenario's primary.
    s = _scenario(
        **_TWO,
        steps=[_step(target="app")],
        interrupts=[{"condition": {"exists": {"id": "popup"}}, "steps": [_step()]}],
    )
    assert s.interrupts[0].steps[0].resolved_target is None


# --- round trip: the resolution never reaches a dump ------------------------------------------


def test_model_dump_never_emits_the_resolved_target() -> None:
    s = _scenario(**_TWO, steps=[_step()])
    assert "target" not in s.steps[0].model_dump(by_alias=True, exclude_none=True)
    snapshot = scenario_dict(s)
    assert snapshot["primaryTarget"] == "app"
    assert "target" not in snapshot["steps"][0]
    # Reloading the dump resolves the same way, so a snapshot stays runnable as written.
    reloaded = Scenario.model_validate(snapshot)
    assert reloaded.steps[0].resolved_target == "app"


def test_copies_keep_the_resolution() -> None:
    step = _scenario(**_TWO, steps=[_step()]).steps[0]
    assert step.model_copy().resolved_target == "app"
    assert step.model_copy(deep=True).resolved_target == "app"


# --- apply_setups: a shared setup's steps are cloned per scenario ------------------------------


def test_scenarios_sharing_a_setup_resolve_their_own_primary() -> None:
    # Without the per-scenario clone, both scenarios would hold the same cached `Step` objects,
    # and the second scenario's re-check would overwrite the first one's resolution.
    scns = [
        _scenario(targets=["app", "web"], primaryTarget="app", steps=[_step()]),
        _scenario(targets=["web", "app"], primaryTarget="web", steps=[_step()]),
    ]
    calls = 0

    def resolve(ref: str) -> list[Step]:
        nonlocal calls
        calls += 1
        return [Step.model_validate({"tap": {"id": "login"}})]

    apply_setups(scns, default_setup="common.yaml", resolve=resolve)
    assert calls == 1  # still resolved once and cached
    assert scns[0].steps[0] is not scns[1].steps[0]
    assert scns[0].steps[0].resolved_target == "app"
    assert scns[1].steps[0].resolved_target == "web"


def test_a_spliced_prelude_drops_a_resolution_from_another_scenario() -> None:
    # The prelude steps were already resolved to 'a' under their own scenario; the re-check under a
    # one-target scenario must clear that, not keep the stale name its copy carried over.
    prelude = _scenario(targets=["a", "b"], primaryTarget="a", steps=[_step()]).steps
    assert prelude[0].resolved_target == "a"
    scns = [_scenario(targets=["c"], steps=[_step()])]
    apply_setups(scns, default_setup="p", resolve=lambda _: prelude)
    assert scns[0].steps[0].resolved_target is None
