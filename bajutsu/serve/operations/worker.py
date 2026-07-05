"""Worker HTTP API serve operations (BE-0106, split out in BE-0127)."""

from __future__ import annotations

import json
from typing import Any

from bajutsu.serve.jobs import ServeState
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.server.object_store import baseline_prefix, org_prefix


def worker_lease(state: ServeState, worker_id: str) -> tuple[dict[str, Any], int]:
    """Lease the oldest queued job for *worker_id*, or return 204 when the queue is empty.

    When the job materializes visual baselines and a hosted object store is configured, the response
    also carries ``baseline_urls`` — one presigned GET URL per baseline under the *leased job's* org
    prefix (BE-0160) — so the worker downloads them over plain HTTP, with no cloud credentials.
    """
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    leased = state.repository.lease_job(worker_id)
    if leased is None:
        return {}, 204
    resp: dict[str, Any] = {"job_id": leased.id, "org_id": leased.org_id, "spec": leased.spec}
    if leased.spec.get("materialize_baselines") and state.object_store is not None:
        resp["baseline_urls"] = _baseline_urls(state, leased.org_id or DEFAULT_ORG)
    return resp, 200


def _baseline_urls(state: ServeState, org: str) -> dict[str, str]:
    """A presigned GET URL per visual baseline for *org*, keyed by baseline name.

    The control plane lists the org's baselines (a credentialed LIST it can do) and signs each — the
    worker never touches the object store directly. Reuses `ObjectBaselineStore.names()` for the
    safe-name listing, so the baseline key scheme keeps one source of truth.
    """
    from bajutsu.serve.server.baselines import ObjectBaselineStore

    assert state.object_store is not None  # caller guards; narrows the type for the signer below
    base = org_prefix(state.object_store_prefix, org)
    store = ObjectBaselineStore(state.object_store, prefix=base)
    return {
        name: state.object_store.presigned_url(f"{baseline_prefix(base)}{name}")
        for name in store.names()
    }


def worker_heartbeat(state: ServeState, worker_id: str, job_id: str) -> tuple[dict[str, Any], int]:
    """Renew a worker's lease mid-run; 409 tells the worker its lease was reclaimed and to stop."""
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    if not job_id:
        return {"error": "job_id is required"}, 400
    if state.repository.heartbeat_job(job_id, worker_id):
        return {"ok": True}, 200
    return {"error": "lease lost or not held by this worker"}, 409


def worker_result(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Record a finished job's result (called by the worker after a run completes)."""
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    job_id = body.get("job_id", "")
    worker_id = body.get("worker_id", "")
    result = body.get("result")
    if not job_id:
        return {"error": "job_id is required"}, 400
    if not worker_id:
        # Required so the leaseholder check below always applies: without it a stale worker whose
        # lease was reclaimed and re-leased could overwrite the winning run.
        return {"error": "worker_id is required"}, 400
    if not isinstance(result, dict):
        return {"error": "result must be a JSON object"}, 400
    info = state.repository.get_job(job_id)
    if info is None:
        return {"error": f"job {job_id} not found"}, 404
    if result.get("ok") is False or "error" in result:
        applied = state.repository.fail_job(
            job_id, error=result.get("error", "unknown"), worker_id=worker_id
        )
    else:
        applied = state.repository.complete_job(job_id, result=result, worker_id=worker_id)
    if not applied:
        # The lease was reclaimed (and maybe re-leased) or the job already finished — this is a stale
        # worker's result, so drop it rather than clobber the winning run, and leave its log stream be.
        return {"error": "job is no longer leased by this worker; result ignored"}, 409
    state.logbus.close(job_id, json.dumps(result))
    return {"ok": True}, 200
