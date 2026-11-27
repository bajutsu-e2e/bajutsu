"""The worker command policy applied to a `hooks` component's commands (BE-0512 unit 5).

Two files bear on a command a component adds, the one that applies the component and the
component's own, so it runs under the stricter mode of their two sources: a component a client
uploaded beside its scenarios never gains the mode of the config that applies it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.capability.command_gate import CommandGate, CommandRefused
from bajutsu.common.capability.command_policy import (
    COMMAND_MODES,
    CommandExecution,
    CommandPolicy,
    stricter_mode,
)
from bajutsu.common.capability.route_tokens import report_hook_phases
from bajutsu.common.config import HookPhases
from bajutsu.common.config.schema.hook_command import HookSandbox
from bajutsu.common.report.html import html_report
from bajutsu.common.report.rows import _name_components
from bajutsu.common.runner import hook_commands as hc
from bajutsu.common.runner.hook_commands import hook_refusal
from bajutsu.common.runner.hook_session import _sandboxes
from bajutsu.common.scenario import Step
from bajutsu.common.scenario.hook_components import apply_hook_component
from bajutsu.common.scenario.models.hook_command import HookCommand
from bajutsu.common.scenario.models.hook_component import HookUse


def _gate(tmp_path: Path, *, uploaded: str = "deny", operator: str = "allow") -> CommandGate:
    execution = CommandExecution.model_validate({"uploaded": uploaded, "operator": operator})
    return CommandGate(
        policy=CommandPolicy(source="operator", execution=execution),
        config_dir=tmp_path,
        scenario_source="uploaded",
        client_roots=(tmp_path / "scenarios",),
    )


def _entry(component: Path | None, cwd: Path | None = None) -> HookCommand:
    data: dict[str, object] = {"cmd": "./seed.sh"}
    if component is not None:
        data["_origin"] = {"component": str(component)}
        data["cwd"] = str(cwd or component.parent)
    return HookCommand.model_validate(data)


# --- the modes ---


@pytest.mark.parametrize(
    ("a", "b", "stricter"),
    [("allow", "sandbox", "sandbox"), ("named", "sandbox", "named"), ("deny", "allow", "deny")],
)
def test_the_stricter_mode_runs_no_more_than_the_other(a: str, b: str, stricter: str) -> None:
    assert stricter_mode(a, b) == stricter  # type: ignore[arg-type]
    assert COMMAND_MODES == ("allow", "sandbox", "named", "deny")


def test_a_component_source_makes_the_policy_take_the_stricter_mode() -> None:
    execution = CommandExecution.model_validate({"uploaded": "named"})
    policy = CommandPolicy(source="operator", execution=execution, component_source="uploaded")
    assert policy.mode == "named"
    assert policy.action("command") == "refuse"
    assert "operator: allow and, for the hooks component file" in policy.refusal("f", "./x")
    assert "uploaded: named, the stricter of the two applying" in policy.refusal("f", "./x")


# --- which source a component file has ---


def test_a_component_among_the_scenario_files_is_judged_by_their_source(tmp_path: Path) -> None:
    gate = _gate(tmp_path).for_entry(str(tmp_path / "scenarios" / "hooks" / "u.hook.yaml"))
    assert gate.policy.component_source == "uploaded"
    assert gate.policy.action("command") == "refuse"
    with pytest.raises(CommandRefused, match="for the hooks component file"):
        gate.check("command", "hooks.beforeScenario[0].cmd", "./seed.sh")
    [record] = gate.records
    assert record["component"] == "scenarios/hooks/u.hook.yaml"
    assert record["componentSource"] == "uploaded"
    assert record["decision"] == "refused"


def test_a_component_in_the_configs_tree_keeps_the_configs_mode(tmp_path: Path) -> None:
    gate = _gate(tmp_path).for_entry(str(tmp_path / "hooks" / "u.hook.yaml"))
    assert gate.policy.component_source is None
    assert gate.check("command", "hooks.beforeScenario[0].cmd", "./seed.sh") == "host"
    gate.record("hooks.beforeScenario[0].cmd", "./seed.sh", exit_code=0)
    [record] = gate.records
    assert record["component"] == "hooks/u.hook.yaml" and "componentSource" not in record


def test_a_scenarios_own_component_from_the_config_tree_joins_the_configs_source(
    tmp_path: Path,
) -> None:
    gate = _gate(tmp_path, uploaded="sandbox", operator="deny").for_scenario()
    judged = gate.for_entry(str(tmp_path / "hooks" / "u.hook.yaml"))
    # The scenario's own source sandboxes, the config's denies: the stricter wins.
    assert judged.policy.source == "uploaded" and judged.policy.component_source == "operator"
    assert judged.policy.mode == "deny"


def test_a_direct_entry_is_judged_as_before(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    assert gate.for_entry(None) is gate


def test_without_a_scenario_source_every_component_takes_the_configs_source(
    tmp_path: Path,
) -> None:
    gate = CommandGate(config_dir=tmp_path, client_roots=(tmp_path,))
    judged = gate.for_entry(str(tmp_path / "u.hook.yaml"))
    assert judged.policy.component_source is None


# --- the check before device work ---


def test_the_preflight_refuses_an_uploaded_components_command(tmp_path: Path) -> None:
    component = tmp_path / "scenarios" / "hooks" / "u.hook.yaml"
    phases = {"web": HookPhases(before_scenario=[_entry(None), _entry(component)])}
    why = hook_refusal(phases, _gate(tmp_path))
    assert why is not None and "hooks.beforeScenario[1].cmd" in why
    assert "for the hooks component file that added it, uploaded: deny" in why


def test_a_sandboxed_component_outside_the_mounted_directory_is_refused(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    outside = tmp_path / "elsewhere" / "u.hook.yaml"
    sandbox = HookSandbox.model_validate({"dockerImage": "alpine"})
    phases = {"web": HookPhases(before_scenario=[_entry(outside)], sandbox=sandbox)}
    gate = CommandGate(
        policy=CommandPolicy(
            source="uploaded", execution=CommandExecution.model_validate({"uploaded": "sandbox"})
        ),
        config_dir=config_dir,
    )
    why = hook_refusal(phases, gate)
    assert why is not None and "cannot run in the sandbox" in why and "outside the" in why
    inside = config_dir / "hooks" / "u.hook.yaml"
    phases = {"web": HookPhases(before_scenario=[_entry(inside)], sandbox=sandbox)}
    assert hook_refusal(phases, gate) is None


def test_a_component_the_policy_sandboxes_starts_its_targets_container(tmp_path: Path) -> None:
    component = tmp_path / "scenarios" / "hooks" / "u.hook.yaml"
    sandbox = HookSandbox.model_validate({"dockerImage": "alpine"})
    gate = _gate(tmp_path, uploaded="sandbox")
    direct_only = {"web": HookPhases(before_scenario=[_entry(None)], sandbox=sandbox)}
    assert (
        _sandboxes(
            direct_only, gate=gate, raw_dir=tmp_path / "raw", base_dir=tmp_path, scenario_targets=()
        )
        == {}
    )
    with_component = {"web": HookPhases(before_scenario=[_entry(component)], sandbox=sandbox)}
    started = _sandboxes(
        with_component, gate=gate, raw_dir=tmp_path / "raw", base_dir=tmp_path, scenario_targets=()
    )
    assert list(started) == ["web"]


# --- reports ---


def test_a_component_entrys_record_names_its_file_in_the_report() -> None:
    record = hc.HookRecord("beforeScenario", "user", "web", None, component="hooks/u.hook.yaml")
    [entry] = hc.hook_record_dicts([record])
    assert entry["component"] == "hooks/u.hook.yaml"
    [block] = report_hook_phases([entry])
    page = html_report("r", [], hook_phases=[block])
    assert "from hooks/u.hook.yaml" in page
    [plain] = hc.hook_record_dicts([hc.HookRecord("beforeScenario", "x", "web", None)])
    assert "component" not in plain


def test_a_component_marks_the_steps_it_adds(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "u.hook.yaml").write_text(
        "setup:\n  - tap: { id: a }\n  - when: { tags: [x] }\n    steps: [{ tap: { id: b } }]\n"
        "cleanup: [{ on: always, steps: [{ tap: { id: c } }] }]\n",
        encoding="utf-8",
    )
    app = apply_hook_component(
        HookUse.model_validate({"hook": "./hooks/u.hook.yaml", "when": {"tags": ["y"]}}),
        root=tmp_path,
        base=tmp_path,
    )
    plain, block = app.setup
    assert [s.component for s in [*(plain.steps or []), *(block.steps or [])]] == [
        "hooks/u.hook.yaml",
        "hooks/u.hook.yaml",
    ]
    assert plain.component is None and block.component is None
    [rule] = app.cleanup
    assert [s.component for s in rule.steps] == ["hooks/u.hook.yaml"]
    # Hidden from authors, but kept across the dump a data-driven row reloads.
    again = Step.model_validate(rule.steps[0].model_dump(by_alias=True, exclude_unset=True))
    assert again.component == "hooks/u.hook.yaml"
    assert "_component" not in Step.model_json_schema().get("properties", {})


def test_the_step_rows_label_each_run_of_component_steps_once() -> None:
    plan: list[dict[str, Any]] = [
        {"_component": "hooks/u.hook.yaml"},
        {"_component": "hooks/u.hook.yaml"},
        {},
        {"_component": "hooks/v.hook.yaml"},
    ]
    rows: list[dict[str, Any]] = [{"detail": {}} for _ in plan]
    _name_components(rows, plan)
    assert [r["detail"].get("component") for r in rows] == [
        "hooks/u.hook.yaml",
        None,
        None,
        "hooks/v.hook.yaml",
    ]


def test_a_missing_sandbox_block_names_the_component_source_that_sandboxed_it(
    tmp_path: Path,
) -> None:
    component = tmp_path / "scenarios" / "hooks" / "u.hook.yaml"
    phases = {"web": HookPhases(before_scenario=[_entry(component)])}
    why = hook_refusal(phases, _gate(tmp_path, uploaded="sandbox"))
    assert why is not None
    assert "operator: allow, and uploaded: sandbox for its hooks component file" in why


def test_a_scenario_level_container_for_the_joined_source_needs_a_component_entry(
    tmp_path: Path,
) -> None:
    # The config's source sandboxes and the scenario's does not: only a scenario whose own entries
    # a component added needs its target's container.
    sandbox = HookSandbox.model_validate({"dockerImage": "alpine"})
    phases = {"web": HookPhases(sandbox=sandbox)}
    gate = _gate(tmp_path, uploaded="allow", operator="sandbox")
    kw: dict[str, Any] = {"gate": gate, "raw_dir": tmp_path / "raw", "base_dir": tmp_path}
    assert _sandboxes(phases, scenario_targets={"web"}, **kw) == {}
    started = _sandboxes(phases, scenario_targets={"web"}, component_targets={"web"}, **kw)
    assert list(started) == ["web"]
