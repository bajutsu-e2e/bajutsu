"""BE-0492: each job carries the source of its config, and the worker's own `worker.yaml` decides
whether that config's commands run.

An uploaded bundle is `uploaded`, a Git config bound at runtime through the API is `remote`
(BE-0121), and a local or startup-bound config is the operator's own. `serve` only labels; the
worker applies its `commandExecution` block, both to the build it runs itself and, through
`--config-source` and `--worker-config`, inside the command it spawns.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc, _post, _serve, project

from bajutsu import serve as srv
from bajutsu.serve.operations.dispatch import (
    _command_needs,
    _config_source,
    _register_and_dispatch,
)
from bajutsu.common.capability.command_policy import CommandExecution
from bajutsu.serve.jobs import _build_app
from bajutsu.serve.server.worker_job import (
    _command_execution,
    _spec_source,
    execute_job_spec,
    job_spec,
)


def _state(tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(runs_dir=tmp_path / "runs", root=tmp_path, cwd=tmp_path)


class _Upload:
    def __init__(self) -> None:
        self.worker_ref = {"id": "a" * 64}


def test_each_binding_maps_to_its_source(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert _config_source(state.binding) == "operator"
    assert _config_source(replace(state.binding, git_from_api=True)) == "remote"
    assert _config_source(replace(state.binding, upload=_Upload())) == "uploaded"  # type: ignore[arg-type]


def test_api_bound_git_config_is_marked_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Binding a Git config through /api/config marks it untrusted; binding a local config after
    # clears the flag, so the trust boundary tracks whichever source is currently active.
    import bajutsu.serve.operations.config as ops
    from bajutsu.common.config_source import Materialized

    _, cfg, runs = project(tmp_path)
    checkout = tmp_path / "gitsrc"
    checkout.mkdir()
    git_cfg = checkout / "bajutsu.config.yaml"
    git_cfg.write_text(
        ("targets:\n  fromgit:\n    platform: ios\n    subject: {id: com.example.fromgit}\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ops, "materialize", lambda spec, **kw: Materialized(git_cfg, checkout, "sha")
    )
    state = srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, _ = _post(port, "/api/config", {"git": "github:acme/repo@main"})
        assert status == 200
        assert state.binding.git_from_api is True
        # A local config bound afterwards is operator-trusted again.
        status, _ = _post(port, "/api/config", {"path": str(cfg)})
        assert status == 200
        assert state.binding.git_from_api is False
    finally:
        server.shutdown()
        server.server_close()


class _Recording:
    def __init__(self) -> None:
        self.jobs: list[srv.Job] = []

    def dispatch(self, _state: srv.ServeState, job: srv.Job) -> None:
        self.jobs.append(job)


def _dispatched(state: srv.ServeState) -> srv.Job:
    state.executor = _Recording()
    job, capped = _register_and_dispatch(state, srv.Job(cmd=["x"]), binding=state.binding)
    assert capped is None and job is not None
    return job


def test_a_dispatched_job_carries_its_binding_source(tmp_path: Path) -> None:
    state = _state(tmp_path)
    assert _dispatched(state).config_source == "operator"
    state.binding = replace(state.binding, git_from_api=True)
    assert _dispatched(state).config_source == "remote"
    state.binding = replace(state.binding, upload=_Upload())  # type: ignore[arg-type]
    assert _dispatched(state).config_source == "uploaded"


def test_a_dispatch_without_a_binding_takes_the_deployments_source(tmp_path: Path) -> None:
    # A caller that omits `binding` spawns against the deployment's (`_freeze_binding`), so the
    # label must come from that binding too rather than defaulting to the operator's.
    state = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    state.executor = _Recording()
    job, _ = _register_and_dispatch(state, srv.Job(cmd=["x"]))
    assert job is not None and job.config_source == "remote"


def test_the_source_travels_in_the_job_spec() -> None:
    spec = job_spec(srv.Job(cmd=["x"], config_source="remote"))
    assert spec["config_source"] == "remote"
    assert _spec_source(spec) == "remote"


@pytest.mark.parametrize(
    ("spec", "source"),
    [
        ({}, "operator"),  # a pre-BE-0492 trusted job
        ({"withhold_runner_build": True}, "uploaded"),  # a pre-BE-0492 untrusted job
        ({"config_source": "elsewhere"}, "uploaded"),  # an unknown label never gains trust
    ],
)
def test_an_unlabelled_or_unknown_spec_reads_strictly(spec: dict[str, Any], source: str) -> None:
    assert _spec_source(spec) == source


def _worker_file(tmp_path: Path, block: str) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(f"version: 1\ndrivers: [fake]\n{block}", encoding="utf-8")
    return path


def test_the_worker_reads_its_modes_from_its_file(tmp_path: Path) -> None:
    allow = _worker_file(tmp_path, "commandExecution: {remote: allow}\n")
    assert _command_execution({"build": "make"}, allow).remote == "allow"
    assert _command_execution({"build": "make"}, None) == CommandExecution()
    # A job with no build never reads the file, as `_can_build` does not.
    assert _command_execution({}, tmp_path / "missing.yaml") == CommandExecution()


@pytest.mark.parametrize(
    ("source", "modes", "builds"),
    [
        ("operator", {}, True),
        ("remote", {}, False),  # the default `sandbox` runs no build
        ("uploaded", {}, False),
        ("remote", {"remote": "allow"}, True),
        ("operator", {"operator": "deny"}, False),
        ("bogus", {"uploaded": "deny", "operator": "allow"}, False),  # unknown reads as uploaded
    ],
)
def test_the_worker_policy_decides_the_build(
    tmp_path: Path, source: str, modes: dict[str, str], builds: bool
) -> None:
    job = srv.Job(
        cmd=["x"],
        app_path="Demo.app",
        build="touch Demo.app",
        config_source=source,
        command_execution=CommandExecution(**modes),  # type: ignore[arg-type]
        bus=srv.InMemoryLogBus(),
        cwd=tmp_path,
    )
    assert _build_app(_state(tmp_path), job) is builds
    assert (tmp_path / "Demo.app").exists() is builds
    if not builds:
        assert job.exit_code == 2


def test_a_refused_build_fails_the_job_naming_the_mode(tmp_path: Path) -> None:
    # The app is missing and the worker's policy does not build an uploaded config's app: the job
    # fails with the reason rather than spawning a run against no binary.
    spec = job_spec(
        srv.Job(
            cmd=["bajutsu", "run"],
            app_path="Demo.app",
            build="touch Demo.app",
            build_platform="ios",
            config_source="uploaded",
        )
    )
    spawned: list[list[str]] = []

    def popen(cmd: list[str], **_kw: Any) -> FakeProc:
        spawned.append(cmd)
        return FakeProc([], 0)

    job = execute_job_spec(
        {**spec, "job_id": "1"}, popen=popen, cwd=tmp_path, bus=srv.InMemoryLogBus()
    )
    assert spawned == [] and job.exit_code == 2
    assert not (tmp_path / "Demo.app").exists()


def test_a_server_command_adds_its_source_requirement(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "targets:\n  web:\n    platform: web\n    subject: {url: 'http://127.0.0.1:8/'}\n"
        "    services: {server: {cmd: ./start.sh}}\n  plain:\n    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:8/'}\n",
        encoding="utf-8",
    )
    uploaded = replace(_state(tmp_path).binding, upload=_Upload())  # type: ignore[arg-type]
    assert list(_command_needs(cfg, "web", uploaded)) == ["command-execution:uploaded"]
    assert _command_needs(cfg, "plain", uploaded) == {}
    assert _command_needs(tmp_path / "missing.yaml", "web", uploaded) == {}
