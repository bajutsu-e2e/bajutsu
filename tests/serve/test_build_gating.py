"""BE-0121: a Git config bound at runtime via the API is untrusted, so its `build:` command runs
only behind the `--allow-remote-build` opt-in — mirroring the `build = None` guard uploaded bundles
already get (BE-0090). A local or startup-bound config stays operator-trusted."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from _shared import FakeProc, _post, _serve, project

from bajutsu import serve as srv
from bajutsu.common.config import RUNNER_BUILD_WITHHELD_ENV
from bajutsu.serve.jobs import _spawn_env
from bajutsu.serve.operations.dispatch import _governed_build, _register_and_dispatch
from bajutsu.serve.server.worker_job import execute_job_spec, job_spec


def _state(tmp_path: Path) -> srv.ServeState:
    return srv.ServeState(runs_dir=tmp_path / "runs", root=tmp_path, cwd=tmp_path)


def test_governed_build_keeps_a_trusted_config_build(tmp_path: Path) -> None:
    # A local/startup config (neither an upload nor an API-bound Git config) is operator-trusted:
    # its build command is left intact.
    state = _state(tmp_path)
    assert _governed_build(state, state.binding, "make build") == "make build"


def test_governed_build_nulls_api_bound_git_build(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    assert _governed_build(state, state.binding, "make build") is None


def test_governed_build_runs_api_bound_git_build_with_opt_in(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    state.allow_remote_build = True
    assert _governed_build(state, state.binding, "make build") == "make build"


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


# --- #2282: driver.runner.build gets the same verdict, enforced inside the spawned run ---------


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


def test_a_trusted_config_keeps_its_runner_build(tmp_path: Path) -> None:
    job = _dispatched(_state(tmp_path))
    assert job.withhold_runner_build is False
    assert RUNNER_BUILD_WITHHELD_ENV not in _spawn_env(job)


def test_an_api_bound_git_config_withholds_its_runner_build(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    job = _dispatched(state)
    assert job.withhold_runner_build is True
    assert _spawn_env(job)[RUNNER_BUILD_WITHHELD_ENV] == "1"


def test_the_remote_build_opt_in_also_allows_the_runner_build(tmp_path: Path) -> None:
    state = _state(tmp_path)
    state.binding = replace(state.binding, git_from_api=True)
    state.allow_remote_build = True
    assert _dispatched(state).withhold_runner_build is False


def test_an_uploaded_bundle_withholds_its_runner_build(tmp_path: Path) -> None:
    class _Upload:
        def __init__(self) -> None:
            self.worker_ref = {"id": "a" * 64}

    state = _state(tmp_path)
    state.binding = replace(state.binding, upload=_Upload())  # type: ignore[arg-type]
    assert _dispatched(state).withhold_runner_build is True


@pytest.mark.parametrize("withhold", [True, False])
def test_a_worker_spawn_carries_the_withheld_runner_build(tmp_path: Path, withhold: bool) -> None:
    # A remote worker rebuilds the job from its spec, so the verdict must travel in it and reach
    # the env of the run the worker spawns.
    spec = job_spec(srv.Job(cmd=["bajutsu", "run"], withhold_runner_build=withhold))
    seen: list[dict[str, str]] = []

    def popen(_cmd: list[str], **kw: Any) -> FakeProc:
        seen.append(kw["env"])
        return FakeProc([], 0)

    execute_job_spec({**spec, "job_id": "1"}, popen=popen, cwd=tmp_path, bus=srv.InMemoryLogBus())
    assert (seen[0].get(RUNNER_BUILD_WITHHELD_ENV) == "1") is withhold
