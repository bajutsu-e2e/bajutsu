"""Worker HTTP API serve operations (BE-0106, split out in BE-0127)."""

from __future__ import annotations

import json
from typing import Any

from bajutsu.serve.helpers import valid_run_id
from bajutsu.serve.jobs import persist_run
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.server.object_store import baseline_prefix, org_prefix
from bajutsu.serve.state import ServeState


def _clean_capabilities(raw: Any) -> list[str]:
    """The worker-advertised capability tokens from a request body, defensively coerced.

    A non-list (or absent) value yields ``[]`` — an un-annotated worker advertises nothing and so
    leases only jobs with no requirement, rather than crashing the lease on a malformed payload.
    Each token is stringified so a stray non-string can't reach the subset test.
    """
    return [str(t) for t in raw] if isinstance(raw, list) else []


def worker_lease(
    state: ServeState, worker_id: str, capabilities: Any = None
) -> tuple[dict[str, Any], int]:
    """Lease the oldest queued job *worker_id* can serve, or return 204 when none is servable.

    *capabilities* is the worker's advertised capability set (BE-0166): the lease is filtered to
    jobs whose required capabilities it covers, so a worker never leases a job it cannot run. The
    worker is registered (its capabilities + liveness recorded) on every poll — including an empty
    one — so an idle worker still counts toward what the pool can route.

    When the job materializes visual baselines and a hosted object store is configured, the response
    also carries ``baseline_urls`` — one presigned GET URL per baseline under the *leased job's* org
    prefix (BE-0160) — so the worker downloads them over plain HTTP, with no cloud credentials.
    """
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    advertised = _clean_capabilities(capabilities)
    state.repository.register_worker(worker_id, advertised)
    leased = state.repository.lease_job(worker_id, advertised)
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
    # Refresh the worker's liveness (BE-0166): a worker busy on a run longer than the lease timeout
    # polls `lease` only after it finishes, so without this its registry row would age out and make
    # its capability's queued jobs look unroutable. The heartbeat is that liveness signal here too.
    state.repository.touch_worker(worker_id)
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
    failed = result.get("ok") is False or "error" in result
    if failed:
        applied = state.repository.fail_job(
            job_id, error=result.get("error", "unknown"), worker_id=worker_id
        )
    else:
        applied = state.repository.complete_job(job_id, result=result, worker_id=worker_id)
    if not applied:
        # The lease was reclaimed (and maybe re-leased) or the job already finished — this is a stale
        # worker's result, so drop it rather than clobber the winning run, and leave its log stream be.
        return {"error": "job is no longer leased by this worker; result ignored"}, 409
    _persist_worker_run(state, info, result, ok=not failed)
    state.logbus.close(job_id, json.dumps(result))
    return {"ok": True}, 200


def _persist_worker_run(
    state: ServeState, info: dict[str, Any], result: dict[str, Any], *, ok: bool
) -> None:
    """Record the run a worker just finished into the system of record (BE-0015 history list).

    The run executed on the worker, but the database is the control plane's: the documented worker
    holds no `BAJUTSU_DATABASE_URL` (docs/self-hosting.md) and its install closure omits the `db`
    extra, so a run's history row had no writer at all and the History list stayed empty on a hosted
    deployment. The identity and label come from the job's own spec, resolved at enqueue — never from
    the worker's payload — and the run id is re-validated because it is worker-supplied and becomes
    both a database id and a storage key. A no-op for a job that produced no run (record/crawl, or a
    build failure).
    """
    run_id = result.get("runId")
    if not isinstance(run_id, str) or not valid_run_id(run_id):
        return
    spec = info.get("spec")
    spec = spec if isinstance(spec, dict) else {}
    actor = spec.get("actor")
    label = spec.get("label")
    persist_run(
        state,
        run_id=run_id,
        org=info.get("org_id") or DEFAULT_ORG,
        actor=actor if isinstance(actor, str) else None,
        label=label if isinstance(label, str) else None,
        ok=ok,
    )
