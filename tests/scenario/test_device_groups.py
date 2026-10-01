"""Tests for device groups in a scenario's `targets` and the `installs` key (BE-0447, unit 1).

Covers the nested `targets` form and its two accessors (`device_groups`, `target_names`), the rules
the scenario model enforces on it (a name once across every group, an array of two or more, the
primary as the first member of the first group, `installs` naming declared members, and a group
the primary does not anchor naming at least one starting member), routing through the flattened
names, the round trip through `model_dump()`, and the staged guard that refuses a device group at
run time until the lease-and-lifecycle unit lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from pydantic import ValidationError

from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.runner import run_all
from bajutsu.common.runner.types import Lease
from bajutsu.common.scenario import (
    Scenario,
    _scenarios_declaring_targets,
    _scenarios_with_device_groups,
    scenario_dict,
)
from bajutsu.run.cli import (
    _check_target_membership,
    _declared_targets_in,
    _reject_device_groups,
)


def _step(**overrides: object) -> dict[str, object]:
    return {"tap": {"id": "a"}, **overrides}


def _scenario(**fields: object) -> Scenario:
    return Scenario.model_validate({"name": "s", "steps": [_step()], **fields})


_UPDATE = {"targets": [["old", "new"]], "primaryTarget": "old"}


# --- the nested form and its accessors ---------------------------------------------------------


def test_a_flat_list_reads_as_groups_of_one() -> None:
    s = _scenario(targets=["app", "web"], primaryTarget="app")
    assert s.device_groups == [["app"], ["web"]]
    assert s.target_names == ["app", "web"]
    assert s.installs == []


def test_a_nested_group_flattens_in_declared_order() -> None:
    s = _scenario(targets=[["app", "auth"], "web"], primaryTarget="app", installs=["auth"])
    assert s.device_groups == [["app", "auth"], ["web"]]
    assert s.target_names == ["app", "auth", "web"]


def test_no_targets_has_no_groups() -> None:
    s = _scenario()
    assert s.device_groups == []
    assert s.target_names == []


def test_a_group_round_trips_unchanged_through_dump() -> None:
    s = _scenario(targets=[["app", "auth"], "web"], primaryTarget="app", installs=["auth"])
    dumped = scenario_dict(s)
    assert dumped["targets"] == [["app", "auth"], "web"]
    assert dumped["installs"] == ["auth"]
    assert Scenario.model_validate(dumped).device_groups == s.device_groups


def test_empty_installs_prunes_from_dump() -> None:
    assert "installs" not in scenario_dict(_scenario(**_UPDATE))


# --- rules on the groups -----------------------------------------------------------------------


def test_a_name_repeated_across_groups_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"targets contains a duplicate name \['app'\]"):
        _scenario(targets=[["app", "auth"], "app"], primaryTarget="app")


def test_a_name_repeated_inside_one_group_is_refused() -> None:
    with pytest.raises(ValidationError, match="targets contains a duplicate name"):
        _scenario(targets=[["app", "app"]], primaryTarget="app")


@pytest.mark.parametrize("group", [["app"], []])
def test_an_array_of_fewer_than_two_names_is_refused(group: list[str]) -> None:
    with pytest.raises(ValidationError, match="holds fewer than two names"):
        _scenario(targets=[group, "web"], steps=[_step(target="web")])


def test_primary_target_must_be_the_first_member_of_the_first_group() -> None:
    with pytest.raises(
        ValidationError, match=r"primaryTarget 'new' must be the first entry of targets \('old'\)"
    ):
        _scenario(targets=[["old", "new"]], primaryTarget="new")


def test_a_step_may_name_any_member_of_a_group() -> None:
    s = _scenario(**_UPDATE, steps=[_step(target="new"), _step()])
    assert [step.resolved_target for step in s.steps] == ["new", "old"]


def test_a_step_naming_an_undeclared_target_is_refused() -> None:
    with pytest.raises(ValidationError, match="is not one of the scenario's declared targets"):
        _scenario(**_UPDATE, steps=[_step(target="other")])


def test_a_group_alone_still_needs_a_primary_for_an_omitted_target() -> None:
    # A group of two is two declared names, so BE-0436's rule holds: an omitted target needs one.
    with pytest.raises(ValidationError, match="target is required — the scenario declares 2"):
        _scenario(targets=[["old", "new"]])


# --- installs ----------------------------------------------------------------------------------


def test_the_primary_group_needs_no_installs_entry() -> None:
    s = _scenario(**_UPDATE)
    assert s.installs == []


def test_listing_the_primary_or_a_bare_name_is_optional() -> None:
    s = _scenario(targets=[["app", "auth"], "web"], primaryTarget="app", installs=["app", "web"])
    assert s.installs == ["app", "web"]


def test_installs_naming_an_undeclared_target_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"installs names \['other'\]"):
        _scenario(**_UPDATE, installs=["other"])


def test_installs_repeating_a_name_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"installs contains a duplicate name \['new'\]"):
        _scenario(**_UPDATE, installs=["new", "new"])


def test_installs_without_targets_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"installs names \['app'\]"):
        _scenario(installs=["app"])


def test_a_group_the_primary_does_not_anchor_must_list_a_member() -> None:
    with pytest.raises(ValidationError, match=r"targets group \['b1', 'b2'\] names none"):
        _scenario(targets=["web", ["b1", "b2"]], primaryTarget="web")


def test_a_group_the_primary_does_not_anchor_passes_with_one_member_listed() -> None:
    s = _scenario(targets=["web", ["b1", "b2"]], primaryTarget="web", installs=["b2"])
    assert s.installs == ["b2"]


def test_the_first_group_needs_no_installs_entry_even_without_primary_target() -> None:
    # The first member of the first group is the primary whether or not `primaryTarget` names it,
    # so that group is always anchored.
    s = _scenario(targets=[["a", "b"], "web"], steps=[_step(target="a"), _step(target="web")])
    assert s.installs == []


# --- the staged run-time guard ------------------------------------------------------------------


def test_device_groups_are_named_only_for_a_group_of_two_or_more() -> None:
    grouped = _scenario(**_UPDATE)
    flat = Scenario.model_validate(
        {"name": "flat", "targets": ["app", "web"], "primaryTarget": "app", "steps": [_step()]}
    )
    assert _scenarios_with_device_groups([grouped, flat]) == ["s"]


def test_a_single_group_counts_as_multi_target() -> None:
    # `[[old, new]]` is one list element but two names; the BE-0428 guard reads the names.
    assert _scenarios_declaring_targets([_scenario(**_UPDATE)]) == ["s"]


def test_run_all_refuses_a_device_group_before_any_lease() -> None:
    eff: Effective = resolve(
        load_config("targets:\n  old: { backend: [fake], bundleId: com.example.app }\n"), "old"
    )

    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("lease must not be called when the device-group guard rejects it")

    with pytest.raises(ValueError, match=r"device groups in targets: are not yet implemented"):
        run_all(eff, [_scenario(**_UPDATE)], lease_must_not_run)


def test_the_run_cli_refuses_a_device_group_with_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as exc:
        _reject_device_groups([_scenario(**_UPDATE)])
    assert exc.value.exit_code == 2
    assert "affected scenario(s): s" in capsys.readouterr().out


def test_the_run_cli_passes_a_scenario_without_groups() -> None:
    _reject_device_groups([_scenario(targets=["app", "web"], primaryTarget="app")])


# --- the run CLI reads the flattened names -------------------------------------------------------


def test_declared_targets_flatten_a_device_group(tmp_path: Path) -> None:
    scn = tmp_path / "g.yaml"
    scn.write_text(
        "- name: g\n  targets: [[old, new], web]\n  primaryTarget: old\n"
        "  steps:\n    - tap: { id: a }\n",
        encoding="utf-8",
    )
    assert _declared_targets_in(scn) == ["old", "new", "web"]


def test_target_membership_accepts_a_member_of_a_group() -> None:
    # `--target new` names a declared target even though it sits inside a group, so the membership
    # check passes and the device-group refusal is what stops the run.
    _check_target_membership([_scenario(**_UPDATE)], "new", explicit=True)


def test_target_membership_refuses_a_name_outside_every_group(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit):
        _check_target_membership([_scenario(**_UPDATE)], "other", explicit=True)
    assert "['old', 'new']" in capsys.readouterr().out
