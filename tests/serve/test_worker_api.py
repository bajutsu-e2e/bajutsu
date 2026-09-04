"""Tests for the worker HTTP API (BE-0106 slice 2 remaining).

The control plane exposes `/api/worker/lease` and `/api/worker/result` so `bajutsu worker` can
lease jobs and return results over HTTP instead of Redis/RQ. Both endpoints are operator-token
authenticated. Tests exercise the operations layer directly (no HTTP server) against an in-memory
SQLite database in the gate and, behind the `postgres` marker, against a real Postgres service in the
serve-db.yml lane (BE-0309) — except the last one, which drives the stdlib handler over a real
socket because the empty-lease 204's wire framing is what it asserts."""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable
from pathlib import Path

from _shared import FakeObjectStore, _serve
from sqlalchemy import Engine, create_engine

from bajutsu import serve as srv
from bajutsu.serve import operations as ops
from bajutsu.serve.server.artifacts import ObjectStorageArtifactStore
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.db_executor import DbQueueExecutor
from bajutsu.serve.server.models import Base
from bajutsu.serve.server.object_store import artifact_prefix, org_prefix
from bajutsu.serve.state import StoreBundle


def _state_with_db(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> tuple[srv.ServeState, SqlRepository]:
    engine = serve_engine()
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs",
        executor=DbQueueExecutor(repo),
        repository=repo,
    )
    return state, repo


def test_worker_lease_returns_spec_when_queued(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
    spec = {"cmd": ["bajutsu", "run"], "job_id": "j1", "udids": []}
    repo.enqueue_job("j1", org_id="o1", spec=spec)
    payload, code = ops.worker_lease(state, "worker-1")
    assert code == 200
    assert payload["job_id"] == "j1"
    assert payload["spec"] == spec


def test_worker_lease_returns_204_when_empty(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _repo = _state_with_db(serve_engine, tmp_path)
    payload, code = ops.worker_lease(state, "worker-1")
    assert code == 204
    assert payload == {}


def test_worker_lease_rejects_empty_worker_id(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _repo = _state_with_db(serve_engine, tmp_path)
    _payload, code = ops.worker_lease(state, "")
    assert code == 400


def test_worker_lease_returns_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_lease(state, "w1")
    assert code == 503


class _FakeStore(FakeObjectStore):
    """The shared in-memory store for the baseline-URL lease tests (BE-0160): a GET URL namespaced
    for this suite, and a listing in a stable order so an assertion can compare it directly."""

    def presigned_url(self, key: str) -> str:
        return f"https://signed.example/get/{key}"

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(prefix))


def test_worker_lease_embeds_baseline_get_urls_under_the_orgs_prefix(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A run that materializes baselines gets a presigned GET URL per baseline, keyed under the
    # *leased job's* org prefix (BE-0160) — so the worker downloads them over plain HTTP, no creds.
    state, repo = _state_with_db(serve_engine, tmp_path)
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


def test_worker_lease_omits_baseline_urls_when_not_materializing(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
    state.object_store = _FakeStore()
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"], "materialize_baselines": False})
    payload, code = ops.worker_lease(state, "w1")
    assert code == 200
    assert "baseline_urls" not in payload


def test_worker_lease_omits_baseline_urls_without_an_object_store(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # Local serve (no hosted object store) never signs baseline URLs, even if a spec asks.
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": ["run"], "materialize_baselines": True})
    payload, code = ops.worker_lease(state, "w1")
    assert code == 200
    assert "baseline_urls" not in payload


def test_worker_result_marks_job_done(serve_engine: Callable[..., Engine], tmp_path: Path) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    result = {"ok": True, "runId": "r1", "summary": {"passed": 3}}
    _payload, code = ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result})
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "done"
    assert info["result"] == result


def test_worker_result_records_the_run_in_the_orgs_history(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A run a remote worker executed shows in the org's History, under the actor and label the
    control plane resolved at enqueue. The documented worker holds no database, so the control plane
    is the only side that can record it — without this the History list stayed empty."""
    state, repo = _state_with_db(serve_engine, tmp_path)
    # The actor must exist for `created_by` to be attributable — a run started by a signed-in user,
    # not the token/CI shape the other tests cover.
    repo.ensure_org("default", slug="default", name="default")
    repo.upsert_user(
        "octocat", org_id="default", github_login="octocat", email="octocat@example.com"
    )
    repo.enqueue_job(
        "j1", org_id="default", spec={"cmd": [], "actor": "octocat", "label": "showcase"}
    )
    repo.lease_job("w1")
    result = {"ok": True, "runId": "20260904-051448"}
    _payload, code = ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": result})
    assert code == 200
    runs, runs_code = ops.runs_payload(state)
    assert runs_code == 200
    assert [r["id"] for r in runs] == ["20260904-051448"]
    assert [(r.id, r.created_by, r.label, r.ok) for r in repo.list_runs(org_id="default")] == [
        ("20260904-051448", "octocat", "showcase", True)
    ]


def test_worker_result_records_a_failed_run_too(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A failing run is history as much as a passing one — the History list is where a red run is
    investigated from, so the failure path must record it too."""
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    repo.lease_job("w1")
    _payload, code = ops.worker_result(
        state,
        {
            "job_id": "j1",
            "worker_id": "w1",
            "result": {"ok": False, "runId": "20260904-052114", "error": "assertion failed"},
        },
    )
    assert code == 200
    assert [(r.id, r.ok) for r in repo.list_runs(org_id="default")] == [("20260904-052114", False)]


def test_worker_result_reads_the_manifest_from_the_orgs_object_prefix(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """The hosted shape, where the bug was seen: the manifest the worker uploaded lives under the
    org's artifact prefix, so the recorded summary must be the real one (scenario names, verdict),
    not the thin fallback a read against the bare default store would produce. A worker that holds
    its own `BAJUTSU_DATABASE_URL` has already written a row from its local manifest by then, so the
    control plane's `record_run` must land on top of it rather than duplicate it; a retried result
    POST is refused as stale before it can reach the persist at all."""
    state, repo = _state_with_db(serve_engine, tmp_path)
    manifest = {
        "runId": "20260904-051448",
        "ok": True,
        "target": "showcase",
        "scenarios": [{"scenario": "smoke.yaml", "ok": True}],
    }
    key = f"{artifact_prefix(org_prefix('tenant/', 'acme'))}20260904-051448/manifest.json"
    store = FakeObjectStore({key: json.dumps(manifest).encode()})
    state.org_stores = lambda org: StoreBundle(
        ObjectStorageArtifactStore(store, prefix=artifact_prefix(org_prefix("tenant/", org))),
        state.scenarios,
        state.baselines,
        state.secrets,
    )
    repo.enqueue_job("j1", org_id="acme", spec={"cmd": [], "label": "showcase"})
    repo.lease_job("w1")
    # The row a DB-holding worker wrote before posting its result, thin because its own upload had
    # not finished when it read the manifest — the control plane's write must replace it in place.
    # The org row is seeded first because `runs.org_id` is a real foreign key on Postgres, and this
    # seed bypasses the `ensure_org` the persist path itself does.
    repo.ensure_org("acme", slug="acme", name="acme")
    repo.record_run(
        RunRecord(
            id="20260904-051448",
            org_id="acme",
            status="done",
            created_by=None,
            ok=True,
            summary={"id": "20260904-051448", "ok": True, "report": False, "scenarios": []},
            label="showcase",
        )
    )
    body = {
        "job_id": "j1",
        "worker_id": "w1",
        "result": {"ok": True, "runId": "20260904-051448"},
    }
    _payload, code = ops.worker_result(state, body)
    assert code == 200
    _retry, retry_code = ops.worker_result(state, body)  # a retried POST is refused as stale
    assert retry_code == 409
    recorded = repo.list_runs(org_id="acme")
    assert [r.id for r in recorded] == ["20260904-051448"]
    assert recorded[0].summary["scenarios"] == ["smoke.yaml"]
    assert recorded[0].summary["report"] is True
    assert recorded[0].target == "showcase"


def test_worker_result_records_a_run_whose_manifest_is_unreadable(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A run whose uploaded `manifest.json` is corrupt still lands in the history, with a thin
    summary: an unreadable manifest must not make the run itself vanish from the list."""
    state, repo = _state_with_db(serve_engine, tmp_path)
    run_dir = tmp_path / "runs" / "20260904-051448"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text("{truncated", encoding="utf-8")
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    repo.lease_job("w1")
    ops.worker_result(
        state,
        {"job_id": "j1", "worker_id": "w1", "result": {"ok": True, "runId": "20260904-051448"}},
    )
    recorded = repo.list_runs(org_id="default")
    assert [r.id for r in recorded] == ["20260904-051448"]
    assert recorded[0].summary["report"] is False


def test_worker_result_records_nothing_for_a_job_with_no_run(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    """A `record` job, and a run that died before minting a run id, produce no history row — and a
    worker-supplied run id that is not a safe single segment is refused, since it becomes both a
    database id and a storage key."""
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="default", spec={"cmd": []})
    repo.lease_job("w1")
    ops.worker_result(state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": True}})
    repo.enqueue_job("j2", org_id="default", spec={"cmd": []})
    repo.lease_job("w1")
    ops.worker_result(
        state, {"job_id": "j2", "worker_id": "w1", "result": {"ok": True, "runId": "../escape"}}
    )
    assert repo.list_runs(org_id="default") == []


def test_worker_result_rejects_missing_job(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _repo = _state_with_db(serve_engine, tmp_path)
    _payload, code = ops.worker_result(state, {"job_id": "nope", "worker_id": "w1", "result": {}})
    assert code == 404


def test_worker_result_requires_worker_id(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _payload, code = ops.worker_result(state, {"job_id": "j1", "result": {"ok": True}})
    assert code == 400


def test_worker_result_returns_503_without_repository(tmp_path: Path) -> None:
    state = srv.ServeState(runs_dir=tmp_path / "runs")
    _payload, code = ops.worker_result(state, {"job_id": "j1", "result": {}})
    assert code == 503


def test_worker_result_rejects_non_dict_result(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _repo = _state_with_db(serve_engine, tmp_path)
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": "not a dict"}
    )
    assert code == 400


def test_worker_result_marks_error_as_failed(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
    repo.enqueue_job("j1", org_id="o1", spec={"cmd": []})
    repo.lease_job("w1")
    _payload, code = ops.worker_result(
        state, {"job_id": "j1", "worker_id": "w1", "result": {"ok": False, "error": "crash"}}
    )
    assert code == 200
    info = repo.get_job("j1")
    assert info is not None
    assert info["status"] == "failed"


def test_worker_lease_then_result_round_trip(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, repo = _state_with_db(serve_engine, tmp_path)
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


def test_worker_lease_204_is_written_without_a_body(tmp_path: Path) -> None:
    """A 204 is framed as zero-length regardless of its headers (RFC 9110 6.4.1), so the empty-queue
    lease must write no body and declare no length — a client that trusts the framing would
    otherwise read the payload as the head of the next response. Lockstep with the FastAPI
    backend, where the same body made uvicorn raise h11's `Too much data for declared
    Content-Length` on every idle worker poll."""
    # A file DB (not in-memory): ThreadingHTTPServer answers on another thread, which SQLite's
    # per-thread connection would otherwise hand an empty database.
    engine = create_engine(f"sqlite:///{tmp_path / 'lease.db'}")
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    state = srv.ServeState(
        runs_dir=tmp_path / "runs", executor=DbQueueExecutor(repo), repository=repo
    )
    server, port = _serve(state)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "POST",
            "/api/worker/lease",
            body=json.dumps({"worker_id": "w1"}),
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 204  # nothing queued
        assert resp.getheader("Content-Length") is None
        assert resp.read() == b""
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
