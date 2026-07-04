"""Worker artifact/scenario presigned-URL serve operations (BE-0160).

The control plane holds the object store's credentials and signs a URL per file, so a worker uploads
a run's artifact tree and a `record` job's authored scenario over plain HTTP with none of its own.
The org is resolved from the *leased job* (never a worker-supplied value), so a worker can't write
into another tenant's prefix; every worker-supplied relative key is re-validated server-side. These
generalize the evidence path (BE-0110) to the artifact and scenario destinations.
"""

from __future__ import annotations

from typing import Any

from bajutsu.object_store import content_type_for
from bajutsu.serve.helpers import valid_relative_key, valid_run_id, valid_scenario_ref
from bajutsu.serve.jobs import ServeState
from bajutsu.serve.operations.presign import sign_put_urls
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.server.object_store import artifact_prefix, org_prefix, scenario_prefix


def _job_org(
    state: ServeState, job_id: Any, worker_id: Any
) -> tuple[str | None, tuple[dict[str, Any], int] | None]:
    """Resolve the org to sign for from the job *worker_id* currently holds a lease on, or an error.

    The org comes from the leased job (looked up server-side), never a worker-supplied value, so
    multi-tenant isolation holds even though the worker relays the job id. Signing is refused unless
    the job is still ``leased`` *by this worker* (mirroring `worker_result`), so a leaked or stale
    job id — a done/failed job, or one another worker holds — can't be replayed to push objects.
    """
    if not isinstance(job_id, str) or not job_id:
        return None, ({"error": "job_id is required"}, 400)
    if not isinstance(worker_id, str) or not worker_id:
        return None, ({"error": "worker_id is required"}, 400)
    if state.repository is None:
        return None, ({"error": "server backend has no database configured"}, 503)
    info = state.repository.get_job(job_id)
    if info is None:
        return None, ({"error": f"job {job_id} not found"}, 404)
    if info.get("status") != "leased" or info.get("leased_by") != worker_id:
        return None, ({"error": "job is not leased by this worker"}, 409)
    return (info.get("org_id") or DEFAULT_ORG), None


def worker_artifact_urls(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Issue one presigned PUT URL per file for uploading a run's artifact tree.

    Keys nest as ``<artifact_prefix(org_base)><run_id>/<file>`` — the org resolved from the leased
    job, then the run id, so runs never collide and a worker stays inside its org's artifact prefix.
    Returns empty ``urls`` when no hosted object store is configured, so a worker can always ask and
    simply upload nothing.

    Args:
        body: ``{"job_id", "worker_id", "run_id", "files": [<relative path>, ...]}`` — the worker's
            leased job and id, the run id, and the relative file paths to sign.
    """
    if state.object_store is None:
        return {"urls": {}}, 200
    org, err = _job_org(state, body.get("job_id"), body.get("worker_id"))
    if err is not None:
        return err
    run_id = body.get("run_id")
    if not isinstance(run_id, str) or not valid_run_id(run_id):
        return {"error": "invalid run_id"}, 400
    base = artifact_prefix(org_prefix(state.object_store_prefix, org or DEFAULT_ORG))
    urls, sign_err = sign_put_urls(state.object_store, f"{base}{run_id}/", body.get("files"))
    if sign_err is not None:
        return sign_err
    return {"urls": urls}, 200


def worker_scenario_url(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Issue one presigned PUT URL for a `record` job's authored scenario.

    The key is ``<scenario_prefix(org_base)><app>/<ref>`` — the org from the leased job — so the
    scenario lands in the same per-project slot the control plane reads. Returns a null ``url`` when
    no hosted object store is configured.

    Args:
        body: ``{"job_id", "worker_id", "app": <project>, "ref": <scenario ref, "*.yaml">}`` — the
            worker's leased job and id, and the project + scenario ref to sign for.
    """
    if state.object_store is None:
        return {"url": None}, 200
    org, err = _job_org(state, body.get("job_id"), body.get("worker_id"))
    if err is not None:
        return err
    app = body.get("app")
    ref = body.get("ref")
    # `app` is a single key segment (reject a slash so a worker can't climb into another project's
    # slot); `ref` is a safe ``*.yaml`` — both re-validated even though the control plane authored them.
    if not isinstance(app, str) or "/" in app or not valid_relative_key(app):
        return {"error": "invalid app"}, 400
    if not isinstance(ref, str) or not valid_scenario_ref(ref):
        return {"error": "invalid scenario ref"}, 400
    base = scenario_prefix(org_prefix(state.object_store_prefix, org or DEFAULT_ORG))
    url = state.object_store.presigned_put_url(
        f"{base}{app}/{ref}", content_type=content_type_for(ref)
    )
    return {"url": url}, 200
