"""`bajutsu run` under the worker's command policy (BE-0492).

The run labels its config with `--config-source`, reads the modes from `--worker-config`, and checks
each command the config declares before any device work. A refused hook command fails every
scenario and exits 2, and the refusal still lands in `manifest.json`. The fake backend stands in for
every device, and the one command the config declares is `false`, so nothing real is started.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from run_cli import invoke
from typer.testing import CliRunner

from bajutsu.cli import app

runner = CliRunner()


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _project(tmp_path: Path, modes: str) -> tuple[Path, Path, Path]:
    port = _dead_port()
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "targets:\n"
        "  site:\n"
        "    platform: web\n"
        f"    subject: {{url: 'http://127.0.0.1:{port}/'}}\n"
        "    hooks:\n"
        "      beforeRun:\n"
        "        - {name: server, cmd: 'false', timeout: 5}\n",
        encoding="utf-8",
    )
    scenario = tmp_path / "smoke.yaml"
    scenario.write_text("scenarios:\n  - name: smoke\n    steps: []\n", encoding="utf-8")
    worker = tmp_path / "worker.yaml"
    worker.write_text(
        f"version: 1\ndrivers: [playwright, fake]\ncommandExecution: {modes}\n", encoding="utf-8"
    )
    return cfg, scenario, worker


def _run(tmp_path: Path, modes: str, source: str) -> tuple[int, str, Path]:
    cfg, scenario, worker = _project(tmp_path, modes)
    runs = tmp_path / "runs"
    result = invoke(
        runner,
        app,
        [
            "run",
            "--target",
            "site",
            "--scenario",
            str(scenario),
            "--backend",
            "fake",
            "--config",
            str(cfg),
            "--worker-config",
            str(worker),
            "--config-source",
            source,
            "--runs-dir",
            str(runs),
        ],
    )
    return result.exit_code, result.output, runs


def test_a_refused_hook_command_fails_the_run_and_is_recorded(tmp_path: Path) -> None:
    code, output, runs = _run(tmp_path, "{uploaded: deny}", "uploaded")
    assert code == 2, output
    assert "hooks.beforeRun[server].cmd" in output and "uploaded: deny" in output
    [manifest] = runs.glob("*/manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False
    [entry] = data["provenance"]["commandExecutions"]
    assert (entry["field"], entry["cmd"], entry["source"], entry["mode"], entry["decision"]) == (
        "hooks.beforeRun[server].cmd",
        "false",
        "uploaded",
        "deny",
        "refused",
    )
    assert "uploaded: deny" in data["scenarios"][0]["failure"]


def test_the_same_config_under_the_operator_source_tries_to_run_it(tmp_path: Path) -> None:
    # The operator's own config is allowed by default, so the run starts the command on the host;
    # `false` exits 1 at once, which the run reports as a failed hook, not a refusal.
    code, output, runs = _run(tmp_path, "{uploaded: deny}", "operator")
    assert code == 2
    assert "exited 1" in output and "does not run" not in output
    # The failed start still leaves its record, with the command's exit code.
    [manifest] = runs.glob("*/manifest.json")
    [entry] = json.loads(manifest.read_text(encoding="utf-8"))["provenance"]["commandExecutions"]
    assert (entry["source"], entry["mode"], entry["decision"], entry["exitCode"]) == (
        "operator",
        "allow",
        "ran",
        1,
    )


def test_an_unknown_source_is_a_usage_error(tmp_path: Path) -> None:
    code, output, _ = _run(tmp_path, "{}", "elsewhere")
    assert code == 2 and "--config-source 'elsewhere'" in output


def test_a_refused_hook_command_fails_before_any_device_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bajutsu.run.cli as run_cli

    def reserved(*_a: object, **_k: object) -> object:
        raise AssertionError("a refused run reserved a device")

    monkeypatch.setattr(run_cli, "_acquire_targets", reserved)
    code, output, runs = _run(tmp_path, "{uploaded: named}", "uploaded")
    assert code == 2 and "uploaded: named" in output
    assert len(list(runs.glob("*/manifest.json"))) == 1


def test_every_declared_entry_is_checked_whatever_its_on_would_match() -> None:
    # The preflight runs before any device work, so it cannot know the verdict an `on` will be
    # judged against; a command the mode refuses fails the run whether or not it would have run.
    from bajutsu.common import config
    from bajutsu.common.capability.command_gate import CommandGate
    from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy
    from bajutsu.common.runner.hook_commands import hook_refusal
    from bajutsu.common.runner.hook_session import phases_of

    eff = config.resolve(
        config.load_config(
            "targets: {web: {platform: web, subject: {url: 'http://127.0.0.1:8/'}, "
            "hooks: {afterRun: [{on: failure, cmd: x}]}}}"
        ),
        "web",
    )
    phases = phases_of({"web": eff})
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded="deny")))
    refusal = hook_refusal(phases, gate)
    assert refusal is not None and "uploaded: deny" in refusal
    assert [r["decision"] for r in gate.records] == ["refused"]
    assert hook_refusal(phases, CommandGate()) is None


def test_a_sandboxed_config_without_a_sandbox_block_names_it() -> None:
    # The container is where a sandboxed command runs, so a target that declares none fails before
    # any device work, naming the block rather than failing inside the bring-up (BE-0491).
    from bajutsu.common import config
    from bajutsu.common.capability.command_gate import CommandGate
    from bajutsu.common.capability.command_policy import CommandExecution, CommandPolicy
    from bajutsu.common.runner.hook_commands import hook_refusal
    from bajutsu.common.runner.hook_session import phases_of

    eff = config.resolve(
        config.load_config(
            "targets: {web: {platform: web, subject: {url: 'http://127.0.0.1:8/'}, "
            "hooks: {beforeRun: [{cmd: x}]}}}"
        ),
        "web",
    )
    gate = CommandGate(policy=CommandPolicy("uploaded", CommandExecution(uploaded="sandbox")))
    refusal = hook_refusal(phases_of({"web": eff}), gate)
    assert refusal is not None and "hooks.sandbox" in refusal


# --- a scenario's own commands (BE-0501) ---


def _run_scenario(
    tmp_path: Path, modes: str, source: str, hooks: str, text: str = "x", extra: Sequence[str] = ()
) -> int:
    cfg, _, worker = _project(tmp_path, modes)
    cfg.write_text(cfg.read_text(encoding="utf-8").split("    hooks:")[0], encoding="utf-8")
    scenario = tmp_path / "flows" / "pay.yaml"
    scenario.parent.mkdir()
    scenario.write_text(
        f"- name: pay\n  hooks: {hooks}\n"
        f"  steps:\n    - type: {{text: '{text}', into: {{id: a}}}}\n",
        encoding="utf-8",
    )
    argv = ["run", "--target", "site", "--scenario", str(scenario), "--backend", "fake"]
    argv += ["--config", str(cfg), "--worker-config", str(worker), "--config-source", source]
    result = invoke(runner, app, [*argv, *extra, "--runs-dir", str(tmp_path / "runs")])
    (tmp_path / "output.txt").write_text(result.output, encoding="utf-8")
    return result.exit_code


def _manifest(tmp_path: Path) -> dict[str, object]:
    [manifest] = (tmp_path / "runs").glob("*/manifest.json")
    data: dict[str, object] = json.loads(manifest.read_text(encoding="utf-8"))
    return data


def test_a_refused_scenario_command_fails_before_any_device_is_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bajutsu.run.cli as run_cli

    def reserved(*_a: object, **_k: object) -> object:
        raise AssertionError("a refused run reserved a device")

    monkeypatch.setattr(run_cli, "_acquire_targets", reserved)
    hooks = "{beforeScenario: [{name: card, cmd: 'false'}]}"
    assert _run_scenario(tmp_path, "{uploaded: deny}", "uploaded", hooks) == 2
    provenance: Any = _manifest(tmp_path)["provenance"]
    [entry] = provenance["commandExecutions"]
    assert entry["field"] == "scenario.hooks.beforeScenario[card].cmd"
    assert entry["decision"] == "refused"


def test_a_run_whose_only_commands_sit_in_a_scenario_runs_them_beside_it(tmp_path: Path) -> None:
    seen = tmp_path / "seen.txt"
    script = tmp_path / "seed.sh"
    script.write_text(f"#!/bin/sh\npwd > {seen}\necho seeded\n", encoding="utf-8")
    script.chmod(0o755)
    hooks = f"{{beforeScenario: [{{name: card, cmd: '{script}'}}]}}"
    _run_scenario(tmp_path, "{}", "operator", hooks)
    assert seen.read_text().strip() == str((tmp_path / "flows").resolve())
    [log] = (tmp_path / "runs").glob("*/hooks/beforeScenario/_scenario/00/card.log")
    assert log.read_text() == "seeded\n"
    provenance: Any = _manifest(tmp_path)["provenance"]
    assert provenance["hooks"][0]["declaredBy"] == "pay"


def test_an_unknown_hook_token_stops_the_run_at_load(tmp_path: Path) -> None:
    hooks = "{beforeScenario: [{name: card, cmd: 'false'}]}"
    assert _run_scenario(tmp_path, "{}", "operator", hooks, "${hooks.nope.x}") == 2
    assert "names no hook" in (tmp_path / "output.txt").read_text()
    assert not list((tmp_path / "runs").glob("*/manifest.json"))


def test_a_scenario_from_elsewhere_answers_to_its_own_source(tmp_path: Path) -> None:
    # The operator's config may run commands; the scenario a client wrote may not.
    hooks = "{beforeScenario: [{name: card, cmd: 'false'}]}"
    extra = ["--scenario-source", "uploaded"]
    assert _run_scenario(tmp_path, "{uploaded: deny}", "operator", hooks, extra=extra) == 2
    provenance: Any = _manifest(tmp_path)["provenance"]
    [entry] = provenance["commandExecutions"]
    assert (entry["source"], entry["mode"], entry["decision"]) == ("uploaded", "deny", "refused")


def test_an_unknown_scenario_source_is_a_usage_error(tmp_path: Path) -> None:
    extra = ["--scenario-source", "elsewhere"]
    assert _run_scenario(tmp_path, "{}", "operator", "{}", extra=extra) == 2
    assert "--scenario-source 'elsewhere'" in (tmp_path / "output.txt").read_text()


def test_a_config_component_among_uploaded_scenarios_answers_to_their_source(
    tmp_path: Path,
) -> None:
    # BE-0512: the operator's config applies a hooks component that sits among the scenario files
    # a client wrote, so the component's command runs under the stricter `uploaded` mode.
    _run_scenario(tmp_path, "{}", "operator", "{}")
    component = tmp_path / "flows" / "hooks" / "seed.hook.yaml"
    component.parent.mkdir()
    component.write_text("beforeScenario: [{name: seed, cmd: 'false'}]\n", encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8")
        + "    hooks:\n      use: [{hook: ./flows/hooks/seed.hook.yaml}]\n",
        encoding="utf-8",
    )
    worker = tmp_path / "worker.yaml"
    worker.write_text(
        worker.read_text().replace("commandExecution: {}", "commandExecution: {uploaded: deny}")
    )
    argv = ["run", "--target", "site", "--scenario", str(tmp_path / "flows" / "pay.yaml")]
    argv += ["--backend", "fake", "--config", str(cfg), "--worker-config", str(worker)]
    argv += ["--config-source", "operator", "--scenario-source", "uploaded"]
    result = invoke(runner, app, [*argv, "--runs-dir", str(tmp_path / "runs2")])
    assert result.exit_code == 2, result.output
    assert "for the hooks component file that added it, uploaded: deny" in result.output
    [manifest] = (tmp_path / "runs2").glob("*/manifest.json")
    [entry] = json.loads(manifest.read_text(encoding="utf-8"))["provenance"]["commandExecutions"]
    assert entry["component"] == "flows/hooks/seed.hook.yaml"
    assert (entry["source"], entry["componentSource"], entry["decision"]) == (
        "operator",
        "uploaded",
        "refused",
    )
