"""The server queues every job and spawns none itself, on every shape it starts in (BE-0449 unit 9).

`LocalExecutor` and the in-process branches are gone, so every supported start wires the queue:
a local start (its SQLite under ``.bajutsu/``), a configured database with no bucket, and a bucket
with a database. A bucket with no database has no queue and is refused at startup. The deprecated
`--backend` flag only warns.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from _shared import patch_gcs_client, project
from cryptography.fernet import Fernet
from typer.testing import CliRunner

from bajutsu import serve as srv
from bajutsu.serve.batch_provider import BatchRequest
from bajutsu.serve.cli import serve as serve_cli
from bajutsu.serve.server.db._functions import upgrade_to_head
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.state import JOB_KINDS


@pytest.fixture(autouse=True)
def _no_ambient_server_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A maintainer's own deployment variables must not decide which shape a case builds.
    for var in (
        "BAJUTSU_SERVER_STORE",
        "BAJUTSU_DATABASE_URL",
        "BAJUTSU_SECRETS_KEY",
        "BAJUTSU_HOSTED",
        "BAJUTSU_REDIS_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _build(tmp_path: Path) -> srv.ServeState:
    _scn, cfg, runs = project(tmp_path)
    return srv._build_state(
        runs_dir=runs,
        config=cfg,
        scenarios_dir=None,
        root=tmp_path,
        baselines_dir=None,
        max_concurrent=0,
        token=None,
    )


def _configured_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The server never migrates a configured database, so the operator's migration runs first.
    url = f"sqlite:///{tmp_path / 'configured.db'}"
    upgrade_to_head(url)
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)
    monkeypatch.setenv("BAJUTSU_SECRETS_KEY", Fernet.generate_key().decode("ascii"))


def _local_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No environment at all: the local defaults."""


def _database_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configured_database(tmp_path, monkeypatch)


def _bucket_and_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    patch_gcs_client(monkeypatch)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bucket/tenant")
    _configured_database(tmp_path, monkeypatch)


def _jobs() -> list[srv.Job]:
    """One job of every kind the server dispatches."""
    jobs = [
        srv.Job(cmd=srv.run_command("smoke.yaml", "demo")),
        srv.Job(cmd=srv.record_command("out.yaml", "demo", "reach the home screen")),
        srv.Job(cmd=srv.crawl_command("demo", out="runs/crawl")),
        srv.Job(cmd=srv.triage_command("runs/r1")),
        srv.Job(
            batch=BatchRequest(
                provider="devicefarm",
                scenario="smoke.yaml",
                target="demo",
                config="bajutsu.config.yaml",
                platform="android",
                app_path="app.apk",
            )
        ),
    ]
    assert {job.kind for job in jobs} == JOB_KINDS  # a new kind must join this guard
    return jobs


@pytest.mark.parametrize("shape", [_local_start, _database_only, _bucket_and_database])
def test_the_server_queues_every_kind_and_spawns_no_child(
    shape: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shape(tmp_path, monkeypatch)
    spawned: list[Any] = []

    def _record_spawn(*args: Any, **kwargs: Any) -> Any:
        spawned.append(args)
        raise AssertionError("the server spawned a job child")

    # Both seams a job child could be spawned through: the state's injected Popen and the module.
    monkeypatch.setattr(subprocess, "Popen", _record_spawn)
    state = _build(tmp_path)
    state.popen = _record_spawn
    assert type(state.executor) is DbQueueExecutor
    assert state.repository is not None
    for job in _jobs():
        registered = state.register(job)
        state.executor.dispatch(state, registered)
        row = state.repository.get_job(registered.id)
        assert row is not None and row["status"] == "queued", job.kind
    assert spawned == []


def test_a_bucket_without_a_database_is_refused_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_gcs_client(monkeypatch)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bucket/tenant")
    with pytest.raises(srv.ServeStartupError, match="needs a database for its queue"):
        _build(tmp_path)
    # Refused before any local default is written.
    assert not (tmp_path / ".bajutsu").exists()


def test_the_cli_reports_a_bucket_without_a_database_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_gcs_client(monkeypatch)
    monkeypatch.setenv("BAJUTSU_SERVER_STORE", "gs://bucket/tenant")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(_serve_app(), ["--port", "8790"])
    assert result.exit_code == 2
    assert "BAJUTSU_DATABASE_URL is unset" in result.output


def _serve_app() -> Any:
    import typer

    app = typer.Typer()
    serve_cli.register(app)
    return app


@pytest.mark.parametrize("value", ["local", "server"])
def test_backend_is_deprecated_and_ignored(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(srv, "serve", lambda **kwargs: calls.append(kwargs))
    result = CliRunner().invoke(_serve_app(), ["--backend", value])
    assert result.exit_code == 0, result.output
    assert serve_cli.BACKEND_DEPRECATION in result.output
    # The flag no longer reaches the server: both values start the same queue-only server.
    assert len(calls) == 1 and "backend" not in calls[0]


def test_no_backend_flag_prints_no_deprecation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "serve", lambda **_kwargs: None)
    result = CliRunner().invoke(_serve_app(), [])
    assert result.exit_code == 0, result.output
    assert "deprecated" not in result.output


def test_an_unknown_backend_still_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(srv, "serve", lambda **_kwargs: pytest.fail("must not start"))
    result = CliRunner().invoke(_serve_app(), ["--backend", "bogus"])
    assert result.exit_code == 2
    assert "unknown --backend 'bogus'" in result.output


def test_serve_sh_starts_a_worker_beside_the_server() -> None:
    # `make serve` keeps working without `--backend`: the script starts the worker that runs every
    # job beside the server, and forwards only the caller's own arguments to `serve`.
    script = (Path(__file__).resolve().parents[2] / "scripts" / "serve.sh").read_text("utf-8")
    assert '"$python_bin" -m bajutsu worker' in script
    # The worker advertises the server's machine identity, read from the state directory beside the
    # runs directory the server uses, so the jobs naming this machine's paths route to it.
    assert '--server-state-dir "$state_dir"' in script
    assert '--runs) runs="$arg"' in script and '--runs=*) runs="${arg#--runs=}"' in script
    # The script re-derives `local_state_dir` in shell. Run its own line against the function, so
    # moving or renaming the state directory on the Python side cannot leave the script behind.
    from bajutsu.serve.server.local_keys import local_state_dir

    (line,) = [ln for ln in script.splitlines() if ln.startswith("state_dir=")]
    for runs in ("runs", "out/runs", "/var/bajutsu/runs"):
        derived = subprocess.run(
            ["bash", "-c", f'runs="$1"; {line}; printf %s "$state_dir"', "-", runs],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert Path(derived) == local_state_dir(Path(runs))
    serve_lines = [line for line in script.splitlines() if "-m bajutsu serve" in line]
    assert serve_lines and all("--backend" not in line for line in serve_lines)
