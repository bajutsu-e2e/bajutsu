"""`bajutsu lint --config --target`: the run's pre-device checks, without a device (BE-0467)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from bajutsu.cli import app
from bajutsu.common.config import Effective, load_config, resolve
from bajutsu.common.target_lint import (
    TargetRequired,
    lint_against_targets,
    offline_actuator,
)

_CONFIG = """\
targets:
  sim:
    platform: ios
    app: {id: com.x}
  phone:
    platform: ios
    app: {id: com.x}
    runsOn: {ios: {kind: device}}
  phone-ok:
    platform: ios
    app: {id: com.x}
    runsOn: {ios: {kind: device}}
    run:
      ignoredSettings:
        erase: "this lane accepts state from earlier runs"
  site:
    platform: web
    app: {url: "http://x/"}
"""

_LOCATION = """\
- name: locate
  preconditions: {run: {erase: true}}
  steps:
    - wait: { for: { id: a }, timeout: 1 }
    - setLocation: { lat: 1, lon: 2 }
"""


def _effective(name: str) -> Effective:
    return resolve(load_config(_CONFIG), name)


def _write(tmp_path: Path, text: str, name: str = "s.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_step_the_route_lacks_is_a_finding_on_its_line(tmp_path: Path) -> None:
    findings = lint_against_targets(_LOCATION, _write(tmp_path, _LOCATION), _effective, "site")
    assert [(f.target, f.line) for f in findings] == [("site", 5)]
    assert "deviceControl.setLocation" in findings[0].message


def test_a_setting_the_route_cannot_apply_is_a_finding(tmp_path: Path) -> None:
    findings = lint_against_targets(_LOCATION, _write(tmp_path, _LOCATION), _effective, "phone")
    messages = [f.message for f in findings]
    assert any("run.erase is not supported on target 'phone'" in m for m in messages)
    # A real device lacks the simctl-backed location override too.
    assert any("deviceControl.setLocation" in m for m in messages)


def test_an_ignored_setting_is_no_finding(tmp_path: Path) -> None:
    findings = lint_against_targets(_LOCATION, _write(tmp_path, _LOCATION), _effective, "phone-ok")
    assert all("run.erase" not in f.message for f in findings)


def test_a_runnable_scenario_has_no_findings(tmp_path: Path) -> None:
    assert lint_against_targets(_LOCATION, _write(tmp_path, _LOCATION), _effective, "sim") == []


def test_a_scenario_with_no_targets_needs_one_named(tmp_path: Path) -> None:
    with pytest.raises(TargetRequired, match="declares no targets"):
        lint_against_targets(_LOCATION, _write(tmp_path, _LOCATION), _effective, None)


def test_declared_targets_are_each_checked_on_their_own_steps(tmp_path: Path) -> None:
    text = """\
- name: both
  targets: [sim, site]
  steps:
    - wait: { for: { id: a }, timeout: 1 }
      target: site
    - setLocation: { lat: 1, lon: 2 }
      target: sim
    - setLocation: { lat: 1, lon: 2 }
      target: site
"""
    findings = lint_against_targets(text, _write(tmp_path, text), _effective, None)
    # Only the step routed to the web target fails, and its line is the authored one, not its
    # place among the steps routed there.
    assert [(f.target, f.line) for f in findings] == [("site", 8)]


def test_a_step_a_component_produced_lands_on_its_use_line(tmp_path: Path) -> None:
    text = """\
components:
  locate:
    steps:
      - wait: { for: { id: a }, timeout: 1 }
      - setLocation: { lat: 1, lon: 2 }
scenarios:
  - name: via component
    steps:
      - wait: { for: { id: b }, timeout: 1 }
      - use: { component: locate }
"""
    findings = lint_against_targets(text, _write(tmp_path, text), _effective, "site")
    assert [f.line for f in findings] == [10]
    assert findings[0].message.startswith("step 3:")


def test_a_precondition_no_driven_platform_has_is_a_finding(tmp_path: Path) -> None:
    text = """\
- name: photos
  preconditions: {device: {ios: {seedPhotos: [a.png]}}}
  steps:
    - wait: { for: { id: a }, timeout: 1 }
"""
    (tmp_path / "a.png").write_bytes(b"")
    findings = lint_against_targets(text, _write(tmp_path, text), _effective, "sim")
    assert any("seedPhotos" in f.message for f in findings)


def test_the_offline_actuator_is_the_backend_s_first_implemented_one() -> None:
    assert offline_actuator(_effective("sim")) == "xcuitest"
    assert offline_actuator(_effective("site")) == "playwright"


# --- the CLI --- #


def _cli(tmp_path: Path, *args: str) -> Result:
    config = _write(tmp_path, _CONFIG, "bajutsu.config.yaml")
    scenario = _write(tmp_path, _LOCATION)
    return CliRunner().invoke(app, ["lint", str(scenario), "--config", str(config), *args])


def test_cli_exits_1_and_names_each_finding(tmp_path: Path) -> None:
    result = _cli(tmp_path, "--target", "site")
    assert result.exit_code == 1
    assert "s.yaml:5: locate [target site]: step 2: setLocation" in result.output


def test_cli_passes_a_runnable_target(tmp_path: Path) -> None:
    result = _cli(tmp_path, "--target", "sim")
    assert result.exit_code == 0
    assert "ok" in result.output


def test_cli_exits_2_when_no_target_can_be_resolved(tmp_path: Path) -> None:
    result = _cli(tmp_path)
    assert result.exit_code == 2
    assert "pass --target" in result.output


def test_cli_exits_2_on_an_unknown_target(tmp_path: Path) -> None:
    assert _cli(tmp_path, "--target", "nope").exit_code == 2


def test_cli_target_without_config_exits_2(tmp_path: Path) -> None:
    scenario = _write(tmp_path, _LOCATION)
    result = CliRunner().invoke(app, ["lint", str(scenario), "--target", "sim"])
    assert result.exit_code == 2


def test_cli_without_config_behaves_as_before(tmp_path: Path) -> None:
    scenario = _write(tmp_path, _LOCATION)
    result = CliRunner().invoke(app, ["lint", str(scenario)])
    assert result.exit_code == 0


def test_cli_exits_1_when_a_component_does_not_expand(tmp_path: Path) -> None:
    config = _write(tmp_path, _CONFIG, "bajutsu.config.yaml")
    scenario = _write(tmp_path, "- name: a\n  steps:\n    - use: { component: missing.yaml }\n")
    result = CliRunner().invoke(
        app, ["lint", str(scenario), "--config", str(config), "--target", "sim"]
    )
    assert result.exit_code == 1
    assert "failed to expand the scenario" in result.output


def test_an_editor_buffer_that_does_not_parse_names_its_file(tmp_path: Path) -> None:
    from bajutsu.common.scenario import load_expanded_scenarios

    with pytest.raises(ValueError, match="invalid YAML in"):
        load_expanded_scenarios(tmp_path / "s.yaml", text="- name: [unclosed\n")


def test_dispatch_routes_an_unreadable_scenario_as_before(tmp_path: Path) -> None:
    from bajutsu.serve.helpers import scenario_route_needs

    config = _write(tmp_path, _CONFIG, "bajutsu.config.yaml")
    assert scenario_route_needs(config, "sim", "", None, tmp_path) == {}
    assert scenario_route_needs(config, "nope", "", _LOCATION, tmp_path) == {}
    assert scenario_route_needs(tmp_path / "absent.yaml", "sim", "", _LOCATION, None) == {}
    needs = scenario_route_needs(config, "sim", "", _LOCATION, None, exclude="x")
    assert "cap:ios-simulator:deviceControl.setLocation" in needs
