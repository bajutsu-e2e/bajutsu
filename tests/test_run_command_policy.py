"""`bajutsu run` under the worker's command policy (BE-0492).

The run labels its config with `--config-source`, reads the modes from `--worker-config`, and checks
each command the config declares before any device work. A refused server command fails every
scenario and exits 2, and the refusal still lands in `manifest.json`. The fake backend stands in for
every device, and the server's readiness URL points at a port nothing listens on, so nothing real
is started.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

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
        "    services:\n"
        "      server: {cmd: 'false', readyTimeout: 1}\n",
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


def test_a_refused_server_command_fails_the_run_and_is_recorded(tmp_path: Path) -> None:
    code, output, runs = _run(tmp_path, "{uploaded: deny}", "uploaded")
    assert code == 2, output
    assert "services.server.cmd" in output and "uploaded: deny" in output
    [manifest] = runs.glob("*/manifest.json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False
    [entry] = data["provenance"]["commandExecutions"]
    assert (entry["field"], entry["cmd"], entry["source"], entry["mode"], entry["decision"]) == (
        "services.server.cmd",
        "false",
        "uploaded",
        "deny",
        "refused",
    )
    assert "uploaded: deny" in data["scenarios"][0]["failure"]


def test_the_same_config_under_the_operator_source_tries_to_run_it(tmp_path: Path) -> None:
    # The operator's own config is allowed by default, so the run starts the command on the host;
    # `false` exits at once, which the run reports as a start-up failure, not a refusal.
    code, output, runs = _run(tmp_path, "{uploaded: deny}", "operator")
    assert code == 2
    assert "exited (code 1)" in output and "does not run" not in output
    assert not list(runs.glob("*/manifest.json"))


def test_an_unknown_source_is_a_usage_error(tmp_path: Path) -> None:
    code, output, _ = _run(tmp_path, "{}", "elsewhere")
    assert code == 2 and "--config-source 'elsewhere'" in output
