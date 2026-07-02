"""Tests for the worker HTTP API (BE-0106 slice 2 remaining).

The control plane exposes `/api/worker/lease` and `/api/worker/result` so `bajutsu worker` can
lease jobs and return results over HTTP instead of Redis/RQ. Both endpoints are operator-token
authenticated. Tests exercise the operations layer directly (no HTTP server) against an in-memory
SQLite database."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.db import SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base


def _state_with_db(tmp_path: Path) -> tuple[srv.ServeState, SqlRepository]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        executor=DbQueueExecutor(repo),
        repository=repo,
    )
    return state, repo


def test_worker_lease_returns_spec_when_queued(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    spec = {"cmd": ["bajutsu", "run"], "job_id": "j1", "udids": []}
    repo.enqueue_job("j1", org_id="o1", spec=spec)
    payload, code = ops.worker_lease(state, "worker-1")
    assert code == 200
    assert payload["job_id"] == "j1"
    assert payload["spec"] == spec


def test_worker_lease_returns_204_when_empty(tmp_path: Path) -> None:
    state, _repo = _state_with_db(tmp_path)
    payload, code = ops.worker_lease(state, "worker-1")
    assert code == 204
    assert payload == {}


def test_worker_result_marks_job_done(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    result = {"ok": True, "runId": "r1", "summary": {"passed": 3}}
    _payload, code = ops.worker_result(state, {"job_id": "j1", "result": result})
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "done"
    assert info["result"] == result


def test_worker_result_rejects_missing_job(tmp_path: Path) -> None:
    state, _repo = _state_with_db(tmp_path)
    _payload, code = ops.worker_result(state, {"job_id": "nope", "result": {}})
    assert code == 404


def test_worker_lease_then_result_round_trip(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"]})
    lease_payload, lease_code = ops.worker_lease(state, "w1")
    assert lease_code == 200
    result = {"ok": True, "runId": "20260702-1"}
    _result_payload, result_code = ops.worker_result(
        state, {"job_id": lease_payload["job_id"], "result": result}
    )
    assert result_code == 200
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done"
