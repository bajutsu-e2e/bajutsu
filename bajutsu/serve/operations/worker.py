"""Worker HTTP API serve operations (BE-0106, split out in BE-0127)."""

from __future__ import annotations

import json
import logging
from typing import Any

from bajutsu.common.evidence.redaction import mask_credential_shapes
from bajutsu.common.handoff import DEFAULT_TIMEOUT_SECONDS, REQUEST_LINE_PREFIX, request_from_json
from bajutsu.serve.helpers import valid_run_id, valid_sha256
from bajutsu.serve.jobs import lease_secret_env, persist_run
from bajutsu.serve.logbus import ARTIFACT_LINE_PREFIX
from bajutsu.serve.orgs import DEFAULT_ORG
from bajutsu.serve.server.object_store import baseline_prefix, org_prefix, upload_store_key
from bajutsu.serve.state import Job, ServeState
from bajutsu.serve.upload_artifacts import ARTIFACT_KINDS, OVERRIDE_KINDS, artifact_store_key

_logger = logging.getLogger(__name__)

# Bounds on one `/api/worker/log` post (BE-0449). Every line is held in the log bus until the job's
# stream expires, so an unbounded post would let one worker grow the server's memory (or a shared
# Redis) at will. A worker flushes about once a second, so 500 lines is far above a normal run's
# rate, and a line past 8 KiB is a dumped payload, not a log line a person reads in the console.
WORKER_LOG_MAX_LINES = 500
WORKER_LOG_MAX_LINE_CHARS = 8192
# The raw-byte bound on one post, enforced before the body is read. A full batch of maximal plain
# lines (500 x 8 KiB, about 3.9 MiB once quoted) fits in one post. JSON escapes a non-ASCII or
# control character to `\uXXXX`, up to 12 bytes per character, so the worker also splits a batch by
# its encoded size (`WORKER_LOG_BATCH_BYTES`) and a well-behaved worker never reaches this bound.
WORKER_LOG_MAX_BODY_BYTES = 4 * 1024 * 1024
# The lines' share of one post; the rest is left for the envelope (`worker_id`, `job_id`, the keys).
WORKER_LOG_BATCH_BYTES = WORKER_LOG_MAX_BODY_BYTES - 64 * 1024
# Published once, in place of the lines a job posts past its live budget of lines or bytes.
LIVE_LOG_TRUNCATED = "[live log truncated; see console.log]\n"

# The live artifacts a worker may post (BE-0449 unit 8): the crawl's screen map alone, the file the
# Crawl tab draws its graph from. A name outside this set is refused, so the route never becomes a
# general channel into the page.
WORKER_ARTIFACT_NAMES = frozenset({"screenmap.json"})
# One snapshot's size, in UTF-8 bytes. A screen map of the crawl's default fifty screens is far
# smaller; a map past this keeps growing on disk, and the page reads that file when the job ends.
WORKER_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024
# The raw-byte bound on one artifact post. JSON escaping can grow the snapshot inside its envelope,
# so the body is allowed more than the snapshot itself, and the snapshot bound above is the real one.
WORKER_ARTIFACT_MAX_BODY_BYTES = 4 * WORKER_ARTIFACT_MAX_BYTES
# One handoff request, screenshot included, as posted by the worker of a paused `record`. A request
# arrives only once the previous one has an answer, so the size bound is the rate bound too.
WORKER_HANDOFF_MAX_BYTES = 8 * 1024 * 1024
WORKER_HANDOFF_MAX_BODY_BYTES = 2 * WORKER_HANDOFF_MAX_BYTES
# How long an answer may wait for the worker to take it: the `record` stops waiting after the same
# time (`handoff.DEFAULT_TIMEOUT_SECONDS`), so an older answer would reach a run that has moved on.
HANDOFF_ANSWER_TTL_SECONDS = int(DEFAULT_TIMEOUT_SECONDS)
# The answer to a caller of a worker relay route that came in on a browser or machine session: the
# relay carries a person's handoff answer and the page's live view, so only a worker may use it.
_WORKER_ONLY = ({"error": "this route is for workers"}, 403)
# The line prefixes only the relay routes may publish.
_RELAY_PREFIXES = (REQUEST_LINE_PREFIX, ARTIFACT_LINE_PREFIX)


def _clean_capabilities(raw: Any) -> list[str]:
    """The worker-advertised capability tokens from a request body, defensively coerced.

    A non-list (or absent) value yields ``[]`` — an un-annotated worker advertises nothing and so
    leases only jobs with no requirement, rather than crashing the lease on a malformed payload.
    Each token is stringified so a stray non-string can't reach the subset test.
    """
    return [str(t) for t in raw] if isinstance(raw, list) else []


def worker_lease(
    state: ServeState, worker_id: str, capabilities: Any = None, *, relay_secrets: bool = True
) -> tuple[dict[str, Any], int]:
    """Lease the oldest queued job *worker_id* can serve, or return 204 when none is servable.

    *capabilities* is the worker's advertised capability set (BE-0166): the lease is filtered to
    jobs whose required capabilities it covers, so a worker never leases a job it cannot run. The
    worker is registered (its capabilities + liveness recorded) on every poll — including an empty
    one — so an idle worker still counts toward what the pool can route.

    When the job materializes visual baselines and a hosted object store is configured, the response
    also carries ``baseline_urls`` — one presigned GET URL per baseline under the *leased job's* org
    prefix (BE-0160) — so the worker downloads them over plain HTTP, with no cloud credentials.

    A job dispatched off an uploaded bundle carries ``bundle_urls`` the same way: a presigned GET URL
    per stored object the bundle is made of, which the worker rebuilds into the workspace it runs the
    job from. Without them the run would start against an ``appPath`` binary only the control plane
    holds. A job naming per-job artifact overrides (BE-0431) carries ``binary_url`` /
    ``scenarios_url`` the same way.

    A cloud-batch job carries ``batch_run_arn`` once an earlier attempt stored its scheduled run, so
    the worker resumes that run instead of submitting again, and ``app_url`` when its application
    was stored at dispatch because the job ships no bundle (BE-0448).

    With *relay_secrets*, the response also carries ``secret_env``: the leased job's org's stored
    secrets (the AI key and the ``${secrets.X}`` values a user set in the UI) under the variables a
    job reads them from (BE-0449). They are read here, at lease time, and never enter the stored
    spec. The route passes False for a caller that came in on a browser or machine session, since
    a worker authenticates with the shared token and nothing else should receive the values. A
    store that cannot be read fails the job rather than run it without them.
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
    if relay_secrets:
        try:
            secret_env = lease_secret_env(state, leased.org_id or DEFAULT_ORG)
        # Any failure of the store, whatever its backend raises, must end the job rather than strand
        # it leased or hand it to the worker without the values it may need.
        except Exception:
            _logger.warning(
                "could not read the stored secrets of org %s", leased.org_id, exc_info=True
            )
            return _fail_unread_secrets(state, leased.id, worker_id), 204
        if secret_env:
            resp["secret_env"] = secret_env
    if leased.spec.get("batch") is not None and (
        run_arn := state.repository.load_batch_run_arn(leased.id)
    ):
        # A re-leased cloud-batch job resumes the run an earlier attempt scheduled (BE-0448), so the
        # worker learns its ARN here rather than through a database connection of its own.
        resp["batch_run_arn"] = run_arn
    if state.object_store is not None:
        org = leased.org_id or DEFAULT_ORG
        if leased.spec.get("materialize_baselines"):
            resp["baseline_urls"] = _baseline_urls(state, org)
        if urls := _bundle_urls(state, org, leased.spec.get("bundle")):
            resp["bundle_urls"] = urls
        resp.update(_override_urls(state, org, leased.spec.get("overrides")))
        if valid_sha256(sha := leased.spec.get("batch_app")):
            # Signed under the leased job's org and re-validated, as `_bundle_urls` explains.
            resp["app_url"] = state.object_store.presigned_url(
                artifact_store_key(state.object_store_prefix, org, "binary", sha)
            )
    return resp, 200


def _fail_unread_secrets(state: ServeState, job_id: str, worker_id: str) -> dict[str, Any]:
    """Fail a just-leased job whose org's secrets could not be read, and end its live stream."""
    assert state.repository is not None  # caller guards
    error = "could not read the org's stored secrets, so the job did not start"
    if state.repository.fail_job(job_id, error=error, worker_id=worker_id):
        state.logbus.publish(job_id, f"error: {error}")
        state.logbus.close(job_id, json.dumps({"ok": False, "error": error}))
    return {}


def _override_urls(state: ServeState, org: str, overrides: Any) -> dict[str, str]:
    """``binary_url`` / ``scenarios_url`` for each per-job artifact override a job names (BE-0431).

    Keyed under the leased job's org and re-validated as full digests, for the same reasons
    `_bundle_urls` gives: the shas come back out of a stored job row, and each becomes a key.
    """
    assert state.object_store is not None  # caller guards; narrows the type for the signer below
    if not isinstance(overrides, dict):
        return {}
    return {
        f"{kind}_url": state.object_store.presigned_url(
            artifact_store_key(state.object_store_prefix, org, kind, sha)
        )
        for kind in OVERRIDE_KINDS
        if valid_sha256(sha := overrides.get(kind))
    }


def _bundle_urls(state: ServeState, org: str, bundle: Any) -> dict[str, str]:
    """Presigned GET URLs for the uploaded bundle a job runs off; empty when it runs off none.

    A single-zip bind (BE-0073) holds its whole tree as one stored zip, signed here under ``bundle``.
    A composed triple (BE-0268) holds one object per supplied leg instead, each signed under its own
    artifact kind, so the worker re-composes the tree and object storage keeps BE-0268's per-leg
    dedup rather than a whole-tree copy per composition. `Upload.worker_ref` decides which shape a
    job carries; this signs whatever it names.

    The org comes from the leased job, never from the worker, so a signed URL can only ever address
    the leaseholder's own tenant prefix — the same rule `_baseline_urls` follows. Every sha is
    re-validated as a full hex digest before it becomes a storage key: it travels through the queue
    and comes back out of a stored job row, so it is not a value freshly computed here.
    """
    assert state.object_store is not None  # caller guards; narrows the type for the signer below
    if not isinstance(bundle, dict) or not valid_sha256(bundle.get("id")):
        return {}
    artifacts = bundle.get("artifacts")
    if artifacts is None:
        key = upload_store_key(state.object_store_prefix, org, bundle["id"])
        return {"bundle": state.object_store.presigned_url(key)}
    if not isinstance(artifacts, dict):
        return {}
    return {
        kind: state.object_store.presigned_url(
            artifact_store_key(state.object_store_prefix, org, kind, sha)
        )
        for kind in ARTIFACT_KINDS
        if valid_sha256(sha := artifacts.get(kind))
    }


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
    """Renew a worker's lease mid-run; 409 tells the worker its lease was reclaimed and to stop.

    Once a cancel has reached the leased job, the 200 response also carries ``"cancel": true``
    (BE-0449): the worker then stops its own run and posts a cancelled result. The lease is still
    renewed, so a `run` that winds down gracefully is not reclaimed while it writes its report.
    """
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
        if state.repository.cancel_requested(job_id, worker_id):
            return {"ok": True, "cancel": True}, 200
        return {"ok": True}, 200
    return {"error": "lease lost or not held by this worker"}, 409


def worker_batch_run(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Store the ARN of the cloud-batch run a leased job scheduled (BE-0448).

    Accepted only from the worker holding the lease, the same owner test as the heartbeat, so a
    worker whose lease was reclaimed cannot overwrite the run the job's next attempt resumes; it gets
    409, stops the run it scheduled, and gives the job up. A repeat of the ARN already stored answers
    200 whoever sends it, so a retry after a lost response never stops a recorded run. The lease
    response of every later attempt carries the stored ARN as ``batch_run_arn``.
    """
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    worker_id = body.get("worker_id", "")
    job_id = body.get("job_id", "")
    run_arn = body.get("run_arn")
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    if not job_id:
        return {"error": "job_id is required"}, 400
    if not isinstance(run_arn, str) or not run_arn.startswith("arn:"):
        return {"error": "run_arn must be an ARN"}, 400
    # Idempotent: a worker whose first save landed but whose answer was lost retries the same ARN,
    # possibly after a reclaim; the run is recorded either way, so it must not be stopped for that.
    if state.repository.load_batch_run_arn(job_id) == run_arn:
        return {"ok": True}, 200
    if not state.repository.save_batch_run_arn(job_id, run_arn, worker_id=worker_id):
        return {"error": "lease lost or not held by this worker"}, 409
    return {"ok": True}, 200


def worker_log(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Publish a batch of a leased job's log lines to the live stream (BE-0449).

    Accepted only from the worker holding the lease, the same owner test as the heartbeat, so a
    worker whose lease was reclaimed cannot write into the stream of the job's next attempt; it gets
    409 and treats it as a lost lease. A batch over `WORKER_LOG_MAX_LINES` is refused whole with 413,
    and a line over `WORKER_LOG_MAX_LINE_CHARS` is cut to that length. Past the job's live budget
    of lines or bytes (`ServeState.live_log_budget`, counted per server process), the lines are
    dropped and one `LIVE_LOG_TRUNCATED` notice is published instead. A line shaped like a handoff
    request or an artifact snapshot is dropped, since those travel on their own routes (unit 8).
    Each line passes the credential-shape
    backstop here too, behind the worker's own masking. The uploaded ``console.log`` stays the
    durable copy of the log; these lines are the live view of it.
    """
    if state.repository is None:
        return {"error": "server backend has no database configured"}, 503
    worker_id = body.get("worker_id", "")
    job_id = body.get("job_id", "")
    lines = body.get("lines")
    if not worker_id:
        return {"error": "worker_id is required"}, 400
    if not job_id:
        return {"error": "job_id is required"}, 400
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        return {"error": "lines must be a list of strings"}, 400
    if len(lines) > WORKER_LOG_MAX_LINES:
        return {"error": f"at most {WORKER_LOG_MAX_LINES} lines per post"}, 413
    if not state.repository.holds_lease(job_id, worker_id):
        return {"error": "lease lost or not held by this worker"}, 409
    # A handoff request and an artifact snapshot travel on their own routes, which check what they
    # carry. A log line shaped like either would reach the page as one, so it is dropped here.
    lines = [line for line in lines if not line.startswith(_RELAY_PREFIXES)]
    # Mask before cutting, so a credential straddling the bound is matched whole, not posted in part.
    masked = [mask_credential_shapes(line)[0][:WORKER_LOG_MAX_LINE_CHARS] for line in lines]
    allowed, crossed = state.live_log_budget.admit(job_id, masked)
    published = masked[:allowed]
    if crossed:
        published.append(LIVE_LOG_TRUNCATED)
    state.logbus.publish_many(job_id, published)
    return {"ok": True}, 200


def worker_result(state: ServeState, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Record a finished job's result (called by the worker after a run completes).

    A result whose ``cancelled`` is true records the job as ``cancelled`` rather than done or
    failed (BE-0449). Like every outcome, it is accepted only from the worker holding the lease.
    """
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
    if result.get("cancelled") is True:
        applied = state.repository.cancel_leased_job(job_id, result, worker_id=worker_id)
    elif failed:
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
    state.live_log_budget.forget(job_id)
    state.live_artifact_budget.forget(job_id)
    state.live_handoff_budget.forget(job_id)
    return {"ok": True}, 200


def _lease_owner(
    state: ServeState, body: dict[str, Any]
) -> tuple[str, str, tuple[Any, int] | None]:
    """The ``(worker_id, job_id)`` of a relay post, or the refusal when either is missing or the
    worker does not hold the job's lease, the owner test every worker route applies."""
    worker_id = body.get("worker_id", "")
    job_id = body.get("job_id", "")
    if state.repository is None:
        return "", "", ({"error": "server backend has no database configured"}, 503)
    if not isinstance(worker_id, str) or not worker_id:
        return "", "", ({"error": "worker_id is required"}, 400)
    if not isinstance(job_id, str) or not job_id:
        return "", "", ({"error": "job_id is required"}, 400)
    if not state.repository.holds_lease(job_id, worker_id):
        return "", "", ({"error": "lease lost or not held by this worker"}, 409)
    return worker_id, job_id, None


def worker_artifact(
    state: ServeState, body: dict[str, Any], *, from_worker: bool = True
) -> tuple[dict[str, Any], int]:
    """Publish a snapshot of a leased job's live artifact to the job's event stream (BE-0449).

    The worker of a crawl posts its screen map each time the file changes, and the page draws the
    graph from the ``artifact`` event this publishes, so the graph grows wherever the worker runs.
    The file the crawl writes stays the durable copy, which the page reads once the job ends.

    Accepted only from the lease holder, as the log route is, and only from a worker: *from_worker*
    is False for a caller that came in on a browser or machine session, which gets 403. The name
    must be one of `WORKER_ARTIFACT_NAMES`, and the content a JSON object of at most
    `WORKER_ARTIFACT_MAX_BYTES`. Past the job's live budget (`ServeState.live_artifact_budget`),
    one ``truncated`` snapshot is published in place of the rest, and the answer carries
    ``"truncated": true`` so the worker stops posting.
    """
    if not from_worker:
        return _WORKER_ONLY
    _worker_id, job_id, refused = _lease_owner(state, body)
    if refused is not None:
        return refused
    name = body.get("name")
    content = body.get("content")
    if name not in WORKER_ARTIFACT_NAMES:
        return {"error": f"name must be one of {sorted(WORKER_ARTIFACT_NAMES)}"}, 400
    if not isinstance(content, str):
        return {"error": "content must be a string"}, 400
    if len(content.encode("utf-8", "surrogatepass")) > WORKER_ARTIFACT_MAX_BYTES:
        return {"error": f"content is over {WORKER_ARTIFACT_MAX_BYTES} bytes"}, 413
    try:
        data = json.loads(content)
    except ValueError:
        return {"error": "content must be JSON"}, 400
    if not isinstance(data, dict):
        return {"error": "content must be a JSON object"}, 400
    # Re-encoded on one line, so the event stream frames it as one `data:` line.
    line = ARTIFACT_LINE_PREFIX + json.dumps({"name": name, "data": data}, separators=(",", ":"))
    allowed, crossed = state.live_artifact_budget.admit(job_id, [line])
    if allowed:
        state.logbus.publish(job_id, line)
        return {"ok": True}, 200
    if crossed:
        state.logbus.publish(
            job_id, ARTIFACT_LINE_PREFIX + json.dumps({"name": name, "truncated": True})
        )
    return {"ok": True, "truncated": True}, 200


def worker_handoff(
    state: ServeState, body: dict[str, Any], *, from_worker: bool = True
) -> tuple[dict[str, Any], int]:
    """Relay a paused `record`'s request for a person from its worker to the page (BE-0449).

    The worker posts the request the `record` printed, and the server marks the job as waiting on
    a person and publishes the request, so the page shows it as a ``human-request`` event, as it
    does for a `record` this process runs. The person's answer goes back through
    `respond_human`, and the worker takes it from `worker_handoff_response`.

    Accepted only from the lease holder and only from a worker (*from_worker*, as for
    `worker_artifact`), only for a `record` job, and only while no earlier request still waits on a
    person (409 otherwise). The request must be a handoff request object of at most
    `WORKER_HANDOFF_MAX_BYTES`, and a job's requests together stay within
    `ServeState.live_handoff_budget` (413 past it).
    """
    if not from_worker:
        return _WORKER_ONLY
    worker_id, job_id, refused = _lease_owner(state, body)
    if refused is not None:
        return refused
    assert state.repository is not None  # `_lease_owner` refused otherwise
    request = body.get("request")
    if not isinstance(request, str):
        return {"error": "request must be a string"}, 400
    if len(request.encode("utf-8", "surrogatepass")) > WORKER_HANDOFF_MAX_BYTES:
        return {"error": f"request is over {WORKER_HANDOFF_MAX_BYTES} bytes"}, 413
    try:
        request_from_json(request)
    except (TypeError, ValueError):  # a non-string `screenshot` fails base64 with a TypeError
        return {"error": "request must be a handoff request object"}, 400
    row = state.repository.get_job(job_id)
    spec = row.get("spec") if row is not None else None
    # Only a `record` pauses for a person; any other kind posting a request is refused.
    if not isinstance(spec, dict) or Job(cmd=list(spec.get("cmd") or [])).kind != "record":
        return {"error": "only a record job hands off to a person"}, 409
    # Re-encoded on one line, as the `record` printed it, so the event stream frames it whole.
    line = REQUEST_LINE_PREFIX + json.dumps(json.loads(request))
    allowed, _crossed = state.live_handoff_budget.admit(job_id, [line])
    if not allowed:
        return {"error": "the job's handoff requests are past their live budget"}, 413
    if not state.repository.open_handoff(job_id, worker_id):
        return {"error": "lease lost, or a handoff is already waiting on a person"}, 409
    state.logbus.publish(job_id, line)
    return {"ok": True}, 200


def worker_handoff_response(
    state: ServeState, body: dict[str, Any], *, from_worker: bool = True
) -> tuple[dict[str, Any], int]:
    """Hand the worker of a paused `record` the person's answer, once there is one (BE-0449).

    The worker polls this while its `record` waits. The answer comes back as ``response``, the
    line the worker writes to the `record`'s standard input, and is removed as it is read. An answer
    older than `HANDOFF_ANSWER_TTL_SECONDS` is dropped, since the `record` stopped waiting by then.
    A cancel that reached the job comes back as ``"cancel": true``, so the worker stops the run at
    once rather than on its next heartbeat.

    Accepted only from the lease holder and only from a worker (*from_worker*, as for
    `worker_artifact`), since the answer may carry a value such as a one-time password.
    """
    if not from_worker:
        return _WORKER_ONLY
    worker_id, job_id, refused = _lease_owner(state, body)
    if refused is not None:
        return refused
    assert state.repository is not None  # `_lease_owner` refused otherwise
    if state.repository.cancel_requested(job_id, worker_id):
        return {"ok": True, "cancel": True}, 200
    token = state.repository.take_handoff_response(job_id, worker_id)
    if token is None or state.handoff_cipher is None:
        return {"ok": True}, 200
    from cryptography.fernet import InvalidToken

    try:
        line = state.handoff_cipher.decrypt(
            token.encode("ascii"), ttl=HANDOFF_ANSWER_TTL_SECONDS
        ).decode("utf-8")
    except (InvalidToken, UnicodeError):
        # Expired, or written under a key this process does not hold; never logged, since it may
        # carry a value the person typed.
        _logger.warning("dropped the handoff answer of job %s: expired or unreadable", job_id)
        return {"ok": True}, 200
    return {"ok": True, "response": line}, 200


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
