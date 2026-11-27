"""The worker command policy applied to a `hooks` component's commands (BE-0512 unit 5).

Two files bear on a command a component adds, the one that applies the component and the
component's own, so it runs under the stricter mode of their two sources: a component a client
uploaded beside its scenarios never gains the mode of the config that applies it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.capability.command_gate import CommandGate, CommandRefused
from bajutsu.common.capability.command_policy import (
    COMMAND_MODES,
    CommandExecution,
    CommandPolicy,
    stricter_mode,
)
from bajutsu.common.config import HookPhases
from bajutsu.common.config.schema.hook_command import HookSandbox
from bajutsu.common.runner.hook_commands import hook_refusal
from bajutsu.common.runner.hook_session import _sandboxes
from bajutsu.common.scenario.models.hook_command import HookCommand


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
