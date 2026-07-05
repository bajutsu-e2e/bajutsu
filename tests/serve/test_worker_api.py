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


def test_worker_lease_rejects_empty_worker_id(tmp_path: Path) -> None:
    state, _repo = _state_with_db(tmp_path)
    _payload, code = ops.worker_lease(state, "")
    assert code == 400


def test_worker_lease_returns_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_lease(state, "w1")
    assert code == 503


class _FakeStore:
    """In-memory `ObjectStore` slice for the baseline-URL lease tests (BE-0160): a signed GET URL
    per key and a prefix listing, so the gate needs no cloud SDK."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.objects[key] = data

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/get/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))


def test_worker_lease_embeds_baseline_get_urls_under_the_orgs_prefix(tmp_path: Path) -> None:
    # A run that materializes baselines gets a presigned GET URL per baseline, keyed under the
    # *leased job's* org prefix (BE-0160) — so the worker downloads them over plain HTTP, no creds.
    state, repo = _state_with_db(tmp_path)
    store = _FakeStore()
    store.put_bytes("o1/baselines/home.png", b"\x89PNG")
    store.put_bytes("o1/baselines/login.png", b"\x89PNG")
    state.object_store = store
    spec = {"cmd": ["bajutsu", "run"], "job_id": "j1", "udids": [], "materialize_baselines": True}
    repo.enqueue_job("j1", org_id="o1", spec=spec)
    payload, code = ops.worker_lease(state, "w1")
    assert code == 200
    assert payload["baseline_urls"] == {
        "home.png": "https://signed.example/get/o1/baselines/home.png",
        "login.png": "https://signed.example/get/o1/baselines/login.png",
    }


def test_worker_lease_omits_baseline_urls_when_not_materializing(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    state.object_store = _FakeStore()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"], "materialize_baselines": False})
    payload, code = ops.worker_lease(state, "w1")
    assert code == 200
    assert "baseline_urls" not in payload


def test_worker_lease_omits_baseline_urls_without_an_object_store(tmp_path: Path) -> None:
    # Local serve (no hosted object store) never signs baseline URLs, even if a spec asks.
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"], "materialize_baselines": True})
    payload, code = ops.worker_lease(state, "w1")
    assert code == 200
    assert "baseline_urls" not in payload


def test_worker_result_marks_job_done(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    result = {"ok": True, "runId": "r1", "summary": {"passed": 3}}
    _payload, code = ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result})
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "done"
    assert info["result"] == result


def test_worker_result_rejects_missing_job(tmp_path: Path) -> None:
    state, _repo = _state_with_db(tmp_path)
    _payload, code = ops.worker_result(state, {"job_id": "nope", "worker_id": "w1", "result": {}})
    assert code == 404


def test_worker_result_requires_worker_id(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _payload, code = ops.worker_result(state, {"job_id": "j1", "result": {"ok": True}})
    assert code == 400


def test_worker_result_returns_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_result(state, {"job_id": "j1", "result": {}})
    assert code == 503


def test_worker_result_rejects_non_dict_result(tmp_path: Path) -> None:
    state, _repo = _state_with_db(tmp_path)
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": "not a dict"}
    )
    assert code == 400


def test_worker_result_marks_error_as_failed(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": False, "error": "crash"}}
    )
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "failed"


def test_worker_lease_then_result_round_trip(tmp_path: Path) -> None:
    state, repo = _state_with_db(tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"]})
    lease_payload, lease_code = ops.worker_lease(state, "w1")
    assert lease_code == 200
    result = {"ok": True, "runId": "20260702-1"}
    _result_payload, result_code = ops.worker_result(
        state, {"job_id": lease_payload["job_id"], "worker_id": "w1", "result": result}
    )
    assert result_code == 200
    info = repo.get_job("j1")
    assert info is not None and info["status"] == "done"
