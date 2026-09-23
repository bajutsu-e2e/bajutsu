"""`bajutsu worker` — lease queued runs from the control plane and execute them (BE-0106).

The hosted control plane (`serve --backend=server`) inserts a job row per run; this command polls
the `/api/worker/lease` endpoint over HTTP, executes the unchanged `run_job`, uploads the run tree
(including `console.log`), and posts the result back to `/api/worker/result`. No Redis or RQ, and
**no cloud credentials** (BE-0160): every object-store touch — downloading baselines before a run,
uploading the run tree and a `record` job's authored scenario after — goes through presigned URLs
the control plane signs, so the worker needs only an HTTP client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer

from bajutsu.common.backend_cli import simctl
from bajutsu.common.backends import PLATFORMS
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.run_meta.files import DEFAULT_RUNS_DIR
from bajutsu.common.run_meta.object_store import content_type_for
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.capabilities import WORKER_CAPABILITIES_ENV, worker_capabilities
from bajutsu.serve.helpers import valid_sha256
from bajutsu.serve.operations.composition import materialize_composition, place_overrides
from bajutsu.serve.server.worker_job import WorkerIO, _materialize, execute_job_spec
from bajutsu.serve.upload_artifacts import ARTIFACT_KINDS, OVERRIDE_KINDS, ArtifactOverrides
from bajutsu.serve.uploads import find_bundle_config, materialize_bundle, validate_bundle_config

_logger = logging.getLogger("bajutsu.worker")

# Heartbeat well under the control plane's default lease timeout (DEFAULT_LEASE_TIMEOUT_SECONDS) so
# a legitimately long run is never mistaken for a dead worker and reclaimed (BE-0016).
DEFAULT_HEARTBEAT_INTERVAL = 30.0

# Per-request timeout for the presigned upload/download paths (BE-0110/BE-0160), so a stalled
# connection can't hang the worker on a single file (evidence upload runs after heartbeats stop).
_UPLOAD_HTTP_TIMEOUT = 60.0

# Name the client instead of leaving urllib's default `Python-urllib/<x.y>`: a control plane behind
# Cloudflare answers that signature with a 403 `error code: 1010` (Browser Integrity Check) before
# the request ever reaches the auth gate, so a correctly-tokened worker leases nothing forever.
_USER_AGENT = "bajutsu-worker"

# Where `_bundle_workspace` keeps one rebuilt tree per uploaded bundle, under the worker's own
# working directory. Dot-prefixed so it is never mistaken for a run's own output, nor picked up by a
# glob over the workspace. Trees nest one level deeper, per org (see `_bundle_workspace`).
_BUNDLE_CACHE_DIR = ".bundles"
# Where a job carrying per-job artifact overrides (BE-0431) gets a tree of its own, beside the bundle
# cache rather than inside it, so a bundle's tree is still fetched once whatever overrides reuse it.
_OVERRIDE_CACHE_DIR = ".overrides"

# The parts a lease may sign for one bundle: the whole tree as a zip (a single-zip bind, BE-0073), or
# one object per artifact kind (a composed triple, BE-0268). A name outside this set is a broken or
# hostile lease response, never a path segment to fetch into.
_BUNDLE_PART_NAMES = frozenset({"bundle", *ARTIFACT_KINDS})

# Read the fetched bytes back in blocks to hash them, so verifying a part never loads an app binary
# into memory (the same reason `_get_file` streams to disk in the first place).
_HASH_CHUNK = 1024 * 1024

# HTTP statuses on a presigned GET that mean the object is not there and never will be, so no retry
# can help. 403 is deliberately absent: S3 answers an expired signature with 403 too, so that one is
# genuinely ambiguous and is better retried than reported.
_GONE_STATUSES = frozenset({404, 410})


class _TransientFetch(Exception):
    """A bundle part could not be *downloaded*, for a reason another attempt may well get past.

    Raised only around the transfer itself (`_get_file` plus the digest check that proves it
    arrived whole), never around building the tree from bytes already on disk. That boundary is the
    classification: a download is at the mercy of the network, while extracting, placing, and
    validating are deterministic over verified bytes, so a failure there is permanent and belongs in
    a reported result. Keying off the exception *type* instead would misfile both directions — an
    `HTTPError` is an `OSError`, and so is the `FileExistsError` a bad tree raises.
    """


def _post_json(
    url: str, body: dict[str, Any], *, token: str | None = None, timeout: float | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode()
    headers: dict[str, str] = {"Content-Type": "application/json", "User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=data, headers=headers)  # noqa: S310
    try:
        with urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
            if not raw:
                return r.status, {}
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError as e:
                # A 2xx that isn't JSON came from in front of the control plane, not from it — a proxy
                # interstitial, or an SSO login page reached through the redirect `urlopen` follows.
                # Raise it as the transport error every caller already handles: returning the text
                # would hand a `str` to callers that read the body as a mapping.
                raise URLError(f"non-JSON response from {url}: {raw[:200]!r}") from e
    except HTTPError as e:
        raw = e.read() if e.fp else b""
        if not raw:
            return e.code, {}
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            # An error page from something in front of the control plane (a proxy, Cloudflare) isn't
            # JSON. Hand back its text with the status rather than raising a decode error that buries
            # the status the caller needs to report.
            return e.code, raw.decode(errors="replace")


def _advertised_capabilities(platform: str, capabilities: str) -> list[str]:
    """The sorted capability set this worker advertises (BE-0166).

    Combines its ``--platform`` axes, the operator override (``--capabilities`` or
    `WORKER_CAPABILITIES_ENV`), and, for an iOS worker, the installed Simulator inventory. The
    Simulator probe is gated on ``ios`` so a web-only worker (the Linux container) never shells out
    to an absent ``xcrun``.
    """
    platforms = [p.strip() for p in platform.split(",") if p.strip()]
    # Fail loudly on a typo'd platform (e.g. `--platform iso`) rather than silently advertising a
    # `platform:iso` token that matches no job — the worker would otherwise poll forever leasing
    # nothing (BE-0166, "determinism first"). Same known set config's `_check_platform` validates.
    if unknown := [p for p in platforms if p not in PLATFORMS]:
        raise typer.BadParameter(
            f"invalid --platform {', '.join(unknown)}: use one of {', '.join(PLATFORMS)}"
        )
    return sorted(
        worker_capabilities(
            platforms,
            override=capabilities or os.environ.get(WORKER_CAPABILITIES_ENV),
            run=simctl.real_run if "ios" in platforms else None,
        )
    )


def worker(
    server_url: str = typer.Option(
        "",
        "--server-url",
        help="Control-plane URL (default: $BAJUTSU_SERVER_URL / http://localhost:8765)",
    ),
    token: str = typer.Option("", "--token", help="Operator token for auth"),
    poll_interval: float = typer.Option(
        2.0, "--poll-interval", help="Seconds between lease attempts when idle"
    ),
    heartbeat_interval: float = typer.Option(
        DEFAULT_HEARTBEAT_INTERVAL,
        "--heartbeat-interval",
        help="Seconds between lease heartbeats during a run (keep it under the server lease timeout)",
    ),
    worker_id: str = typer.Option("", "--worker-id", help="Worker identifier"),
    platform: str = typer.Option(
        "ios",
        "--platform",
        help="Comma-list of platforms this worker can drive (ios / web / android) — the backend "
        "axis it advertises for capability routing (BE-0166). A Mac iOS worker is 'ios'; the "
        "Playwright container is 'web'.",
    ),
    capabilities: str = typer.Option(
        "",
        "--capabilities",
        help="Extra capability tokens to advertise beyond the platform + Simulator inventory "
        "(comma/space separated, e.g. 'ios18,ipad'); also read from $BAJUTSU_WORKER_CAPABILITIES.",
    ),
) -> None:
    """Run a worker that leases queued `bajutsu run` jobs from the control plane over HTTP.

    Polls POST /api/worker/lease; on a job, runs execute_job_spec, uploads the run tree, and
    posts the result to POST /api/worker/result.
    """
    # Both drive sleeps/timeouts; a non-positive value would spin the poll or heartbeat loop hot.
    if poll_interval <= 0:
        raise typer.BadParameter("--poll-interval must be positive")
    if heartbeat_interval <= 0:
        raise typer.BadParameter("--heartbeat-interval must be positive")

    url = server_url or os.environ.get("BAJUTSU_SERVER_URL") or "http://localhost:8765"
    auth_token = token or os.environ.get("BAJUTSU_TOKEN") or None
    wid = worker_id or f"worker-{os.getpid()}"
    work = Path.cwd()
    # The capability set this worker advertises on every lease (BE-0166), computed once at startup
    # (the pool is assumed stable per worker).
    caps = _advertised_capabilities(platform, capabilities)

    typer.echo(f"bajutsu worker → polling {url}  (Ctrl-C to stop)")
    typer.echo(f"  advertising capabilities: {', '.join(caps) or '(none)'}")
    while True:
        try:
            code, body = _post_json(
                f"{url}/api/worker/lease",
                {"worker_id": wid, "capabilities": caps},
                token=auth_token,
            )
        except (URLError, OSError) as e:
            _logger.warning("lease request failed: %s", e)
            time.sleep(poll_interval)
            continue

        # Say why a lease was refused instead of polling on in silence: a rejected credential or a
        # proxy blocking the request looks exactly like an empty queue otherwise.
        if code >= 400:
            # Truncated: a refusal never self-resolves, so this repeats every poll, and a WAF's
            # error page is kilobytes of HTML — one refusal must stay one readable line.
            _logger.warning("lease refused with HTTP %s: %.200s", code, body)
            time.sleep(poll_interval)
            continue

        if code == 204 or not body.get("spec"):
            time.sleep(poll_interval)
            continue

        job_id = body["job_id"]
        spec = body["spec"]
        typer.echo(f"  leased job {job_id}")

        # The worker's object I/O is brokered by presigned URLs (BE-0160): the lease already carries
        # signed GET URLs for this run's baselines, and this io asks the control plane for signed PUT
        # URLs when uploading the run tree / authored scenario — so the worker holds no credentials.
        io = PresignedWorkerIO(
            url=url,
            auth_token=auth_token,
            job_id=job_id,
            worker_id=wid,
            baseline_urls=body.get("baseline_urls"),
        )
        bus = InMemoryLogBus()
        result, abandoned, job_work = _run_with_heartbeat(
            spec,
            job_id=job_id,
            work=work,
            bundle_urls=body.get("bundle_urls"),
            override_urls=_override_urls(body),
            bus=bus,
            url=url,
            wid=wid,
            auth_token=auth_token,
            heartbeat_interval=heartbeat_interval,
            io=io,
        )
        if abandoned:
            # The control plane reclaimed and likely re-leased this job to another worker; posting a
            # result would race that worker, so drop it (the re-run is the source of truth).
            typer.echo(f"  lease lost for job {job_id}; abandoning")
            continue
        if result is None:
            # The bundle fetch hit a transient failure (a reset connection, an expired signature) —
            # logged already, inside `_run_with_heartbeat`. Posting a failure here would turn one
            # network blip into a red run for a scenario that never executed; leaving the lease to
            # lapse instead gives the queue's own reclaim-and-retry a chance to hand the job to
            # another attempt (directive 2: keep worker-side flakiness out of the verdict).
            typer.echo(
                f"  bundle fetch failed transiently for job {job_id}; leaving it to be re-leased"
            )
            continue

        run_id = result.get("runId")
        if run_id:
            _write_console_log(job_work, run_id, bus, job_id)

        _post_result(url, job_id, wid, result, auth_token)

        # Upload the run's evidence via presigned URLs the control plane signs (BE-0110): the worker
        # holds no cloud credentials of its own. Runs *after* the result is posted — heartbeats stop
        # once the run returns, so a slow upload must not delay the post and risk the lease being
        # reclaimed. Best-effort and time-bounded: a failure or stall warns and never affects the run.
        if run_id:
            _upload_evidence(
                job_work,
                run_id,
                url=url,
                auth_token=auth_token,
                evidence_prefix=str(spec.get("evidence_prefix") or ""),
            )

        # Say which way it went: a run that died on its first line ("No module named bajutsu")
        # otherwise looked exactly like a pass from this console.
        typer.echo(f"  completed job {job_id} ({'ok' if result.get('ok') else 'FAILED'})")


def _run_with_heartbeat(
    spec: dict[str, Any],
    *,
    job_id: str,
    work: Path,
    bundle_urls: Any,
    bus: InMemoryLogBus,
    url: str,
    wid: str,
    auth_token: str | None,
    heartbeat_interval: float,
    io: WorkerIO | None = None,
    override_urls: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, bool, Path]:
    """Run the job on a background thread while heart-beating its lease from this one.

    The bundle fetch (`_workspace_or_failure`) runs on this same thread, ahead of `execute_job_spec`,
    so the heartbeat covers it too. It is the single largest transfer in the whole job — an app
    binary, up to the 1 GiB on-the-wire upload cap — and it used to run *before* this function
    was even called, with nothing renewing the lease while it downloaded; a fetch slower than the
    lease timeout tripped a reclaim this worker never noticed, and ran the job anyway alongside
    whichever worker won the re-lease. Object I/O (baseline download, run-tree/scenario upload) runs
    here too through *io*, for the same reason.

    Returns ``(result, abandoned, job_work)``. *abandoned* is True when the control plane reclaimed
    the lease mid-run (HTTP 409): another worker now owns the job, and *result* should be dropped.
    A `None` *result* means the bundle fetch failed transiently (already logged): nothing to post,
    and the lease is left to lapse rather than reported as a failed job (directive 2 — a network
    blip must not surface as a red run for a scenario that never executed). *job_work* is *work*
    itself for a job that ships no bundle, or meaningless alongside a `None` result.

    A reclaim seen while the fetch is still running must stop the run from ever starting, not merely
    get dropped once it finishes: without *lost*, the run thread would walk straight from a finished
    fetch into `execute_job_spec` — installing the app and driving the device beside whichever worker
    won the re-lease — and only discard the result afterward. The fetch is the one point past which
    nothing has touched the device yet, so returning early there costs nothing.
    """
    holder: dict[str, Any] = {"work": work}
    lost = threading.Event()

    def _run() -> None:
        try:
            job_work, failure = _workspace_or_failure(work, spec, bundle_urls, override_urls)
        except _TransientFetch as e:
            _logger.warning(
                "bundle fetch failed for job %s; leaving the lease to lapse: %s", job_id, e
            )
            holder["transient"] = True
            return
        holder["work"] = job_work
        if failure is not None:
            holder["result"] = failure
            return
        if lost.is_set():  # the lease is already gone — never start the run
            return
        try:
            job = execute_job_spec(
                spec,
                popen=subprocess.Popen,
                simctl=simctl.real_run,
                cwd=job_work,
                bus=bus,
                io=io,
            )
            result = job.view()
            result.pop("lines", None)
            holder["result"] = result
        except Exception as e:  # a worker keeps running past one job's failure
            _logger.exception("job %s failed", job_id)
            holder["result"] = {"ok": False, "error": str(e)}

    runner = threading.Thread(target=_run, daemon=True)
    runner.start()
    abandoned = False
    while runner.is_alive():
        runner.join(timeout=heartbeat_interval)
        if not runner.is_alive():
            break
        try:
            code, _ = _post_json(
                f"{url}/api/worker/heartbeat",
                {"worker_id": wid, "job_id": job_id},
                token=auth_token,
                # Bounded by one interval: without a timeout a stalled connection blocks this loop
                # indefinitely and the lease lapses anyway — the failure this function exists to
                # prevent, arriving through the renewal path itself.
                timeout=heartbeat_interval,
            )
        except (URLError, OSError) as e:
            _logger.warning("heartbeat failed for job %s: %s", job_id, e)
            continue
        if code == 409:
            abandoned = True
            lost.set()  # seen in time, this stops _run before it ever calls execute_job_spec
            runner.join()  # wait it out so this worker never runs two jobs at once
            break

    if holder.get("transient"):
        return None, abandoned, holder["work"]
    result = holder.get("result", {"ok": False, "error": "worker produced no result"})
    return result, abandoned, holder["work"]


def _write_console_log(work: Path, run_id: str, bus: InMemoryLogBus, job_id: str) -> None:
    """Write the job's buffered log to runs/<run_id>/console.log for upload.

    The log is whatever the run printed, so it goes through the sink like every other run artifact
    (BE-0331). A worker holds no secret values of its own, so the redactor is inert and only the
    sink's pattern backstop — which needs no configuration — reaches this text.
    """
    run_dir = work / DEFAULT_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return
    lines = list(bus.stream(job_id, timeout=0.0))
    if not lines:
        return
    RunArtifactWriter(run_dir, Redactor(None)).write_text(
        "console.log", "".join(line for line in lines if line is not None)
    )


def _evidence_files(run_dir: Path) -> list[str]:
    """Relative POSIX paths of every real file under *run_dir* (the keys an upload endpoint signs).

    Shared by the artifact and evidence uploads. Symlinks and non-files are skipped, and each
    resolved path must stay under the run dir, so nothing outside the tree is offered for upload.
    """
    base = run_dir.resolve()
    files: list[str] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            continue
        files.append(resolved.relative_to(base).as_posix())
    return files


def _put_file(url: str, path: Path, content_type: str, *, timeout: float | None = None) -> None:
    """Upload one file to a presigned PUT *url*, streaming it from disk.

    The Content-Type must match what the control plane signed into the URL (the presigned signature
    covers it), so send the same value. *timeout* bounds a stalled connection. The file is streamed
    (``http.client`` reads the open handle in blocks) with an explicit Content-Length, so a large run
    artifact like a video never loads wholly into memory.
    """
    headers = {"Content-Length": str(path.stat().st_size), "User-Agent": _USER_AGENT}
    if content_type:
        headers["Content-Type"] = content_type
    with path.open("rb") as body:
        req = Request(url, data=body, method="PUT", headers=headers)  # noqa: S310
        with urlopen(req, timeout=timeout):  # noqa: S310
            pass


def _get_file(url: str, dest: Path, *, timeout: float | None = None) -> None:
    """Download a presigned GET *url* into *dest*, streaming it to disk.

    Streamed rather than read whole: a baseline is a small image, but a bundle zip carries a built
    app binary and would otherwise sit in memory in full.
    """
    req = Request(url, method="GET", headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urlopen(req, timeout=timeout) as r, dest.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(r, out)


def _request_upload_urls(
    url: str, endpoint: str, body: dict[str, Any], auth_token: str | None
) -> dict[str, Any]:
    """Ask the control plane for a presigned PUT URL per file (BE-0110 evidence, BE-0160 artifacts).

    Returns the ``{rel: url}`` mapping. Raises on a transport/HTTP error or a malformed response, so
    the caller decides whether to swallow it (evidence, uploaded after the verdict) or fail the run
    (artifacts). An empty mapping means the destination isn't configured — nothing to upload.
    """
    code, resp = _post_json(
        f"{url}{endpoint}", body, token=auth_token, timeout=_UPLOAD_HTTP_TIMEOUT
    )
    if code != 200:
        raise RuntimeError(f"{endpoint} returned {code}")
    urls = resp.get("urls")
    if not isinstance(urls, dict):
        raise RuntimeError(f"{endpoint} returned an unexpected response shape")  # noqa: TRY004  # invalid external payload, not a caller type error
    return urls


def _put_tree_files(run_dir: Path, urls: dict[str, Any], *, best_effort: bool) -> int:
    """PUT each ``rel -> presigned url`` file under *run_dir*, returning how many uploaded.

    Each returned key is confined under *run_dir* and required to be a string URL, so a malformed or
    hostile response can't read files outside the tree. With *best_effort* a bad entry or a failed
    PUT is logged and skipped (evidence, already past the verdict); otherwise it raises — a report
    artifact the control plane can't serve must fail the run loudly, not vanish (BE-0160).
    """
    base = run_dir.resolve()
    uploaded = 0
    for rel, put_url in urls.items():
        # Require a string key + URL and confine the key under the run dir, so a malformed or hostile
        # response can neither crash the loop (a non-string key would blow up the path-join) nor read
        # a file outside the tree. Check the types before the join so a bad key hits this guard.
        ok = isinstance(rel, str) and isinstance(put_url, str)
        src = (run_dir / rel).resolve() if ok else base
        if not ok or not src.is_relative_to(base):
            if not best_effort:
                raise RuntimeError(f"unexpected upload entry {rel!r}")
            _logger.warning("skipping unexpected upload entry %r under %s", rel, run_dir)
            continue
        try:
            _put_file(put_url, src, content_type_for(rel), timeout=_UPLOAD_HTTP_TIMEOUT)
        except Exception:
            if not best_effort:
                raise
            _logger.warning("upload failed for %s", rel)
        else:
            uploaded += 1
    return uploaded


def _post_result(
    url: str, job_id: str, worker_id: str, result: dict[str, Any], auth_token: str | None
) -> None:
    """Post a finished (or unstartable) job's result; a transport failure is logged, never raised."""
    try:
        _post_json(
            f"{url}/api/worker/result",
            {"job_id": job_id, "result": result, "worker_id": worker_id},
            token=auth_token,
        )
    except (URLError, OSError):
        _logger.exception("result post failed for job %s", job_id)


def _safe_org(org: Any) -> str:
    """*org* reduced to one safe path segment for the bundle cache, or ``default`` when unusable.

    The org travels in the job spec, so it is server-authored — but it becomes a directory name here,
    and a leased spec is still remote input. An allowlist of the characters an org id may hold keeps
    a separator or a `..` out of the path rather than trusting the value's provenance.

    An org id is operator-authored (`orgs:` or the database) and already reaches object-store keys
    unsanitized through `org_prefix`, so a space, a non-ASCII character, or a 70-character id is legal
    upstream — and stripping it down here can't be allowed to collide two such ids onto one directory.
    An id returns as-is only when it is already a safe segment: nothing stripped, at most 64
    characters, and lowercase. Anything else — a stripped character, an over-long id, or any
    uppercase — is disambiguated by a digest of the *raw* value instead, so two ids that would
    otherwise share one segment (`"acme corp"` / `"acmecorp"`, two ids agreeing on their first 64
    characters, or `"Acme"` / `"acme"` on a case-insensitive filesystem, which is the default on a
    macOS worker) never share the mutable tree underneath — the isolation this cache exists to give
    each tenant.

    The digest encodes with ``surrogatepass`` because a lone surrogate survives `json.loads` into a
    `str` that strict UTF-8 refuses: without it this sanitizer would raise, and
    `_workspace_or_failure` would classify that as a permanent failure and post a red run for a
    scenario that never executed. Surrogates are the only strs strict UTF-8 rejects, and the
    error handler is injective over them, so collision resistance is unchanged.
    """
    raw = org if isinstance(org, str) else ""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", raw).strip(".")
    if cleaned == raw and cleaned == cleaned.lower() and len(cleaned) <= 64:
        return cleaned or "default"
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{cleaned[:51] or 'org'}-{digest[:12]}"


def _override_urls(lease: dict[str, Any]) -> dict[str, str]:
    """The lease's signed override GETs (``binary_url`` / ``scenarios_url``), keyed by kind."""
    return {
        kind: url for kind in OVERRIDE_KINDS if isinstance(url := lease.get(f"{kind}_url"), str)
    }


def _workspace_or_failure(
    work: Path,
    spec: dict[str, Any],
    bundle_urls: Any,
    override_urls: dict[str, str] | None = None,
) -> tuple[Path, dict[str, Any] | None]:
    """The workspace to run this job from, or the failed result to post when none can be prepared.

    A job whose bundle cannot be fetched for a reason no worker can ever get past — an invalid
    bundle id, a lease that signed no url, a bundle that fails validation — is reported as failed
    rather than left to crash the poll loop: the control plane would otherwise keep re-leasing a job
    this worker can never start, and the user would wait on a run that never returns a verdict. The
    returned path is meaningless when a failure comes back with it.

    Raises `_TransientFetch` instead when the *download* failed — a reset connection, a stalled
    socket, a body that arrived truncated: another worker, or this one a minute later, would likely
    succeed, so the caller lets the lease lapse rather than turning one network blip into a
    permanent failed job. Everything after the download is deterministic over verified bytes, so it
    keeps the permanent classification.
    """
    try:
        base = _bundle_workspace(work, spec, bundle_urls)
    except _TransientFetch:
        raise
    except Exception as e:
        _logger.exception("could not materialize the job's bundle")
        return work, {"ok": False, "error": f"bundle unavailable: {e}"}
    try:
        return _override_workspace(work, base, spec, override_urls or {}), None
    except _TransientFetch:
        raise
    except Exception as e:
        _logger.exception("could not place the job's artifact overrides")
        return work, {"ok": False, "error": f"artifact override unavailable: {e}"}


def _job_overrides(spec: dict[str, Any]) -> ArtifactOverrides | None:
    """The job's per-job artifact overrides (BE-0431), re-validated, or None when it names none.

    Server-authored, but each sha becomes a directory key and is checked against a download, and a
    leased spec is still remote input.
    """
    raw = spec.get("overrides")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        # The spec's provenance still names the overrides, so running the binding would misreport it.
        raise RuntimeError(f"job carries malformed overrides: {raw!r}")  # noqa: TRY004  # invalid external payload, not a caller type error
    shas = {kind: raw.get(kind) for kind in OVERRIDE_KINDS if raw.get(kind) is not None}
    if not shas:
        return None
    for kind, sha in shas.items():
        if not valid_sha256(sha):
            raise RuntimeError(f"job carries an invalid {kind} override: {sha!r}")
    target = raw.get("target")
    if not isinstance(target, str) or not target:
        raise RuntimeError("job carries artifact overrides but names no target")
    return ArtifactOverrides(target, binary=shas.get("binary"), scenarios=shas.get("scenarios"))


def _override_workspace(work: Path, base: Path, spec: dict[str, Any], urls: dict[str, str]) -> Path:
    """The tree a job carrying artifact overrides runs from, or *base* when it carries none.

    The overrides are placed into a tree of the job's own — a copy of the cached bundle's, or a
    fresh directory for a materials-based job — never into *base*: *base* is shared with every
    later job off the same bundle, or is the worker's own working directory, and an override left
    there would reach the next job with nothing announcing it. The tree is keyed by what it was built
    from, the target, and the overrides' identity, so an identical job reuses it and two jobs that
    differ in any of them never share one. Built aside and renamed into place, so a failed fetch or
    placement leaves nothing a later job could mistake for a finished tree.
    """
    overrides = _job_overrides(spec)
    if overrides is None:
        return base
    bundle = spec.get("bundle")
    raw_materials = spec.get("materials")
    materials: dict[str, str] = raw_materials if isinstance(raw_materials, dict) else {}
    source = (
        bundle["id"]
        if isinstance(bundle, dict)
        else hashlib.sha256(json.dumps(materials, sort_keys=True).encode()).hexdigest()
    )
    key = hashlib.sha256(f"{source}:{overrides.target}:{overrides.identity}".encode()).hexdigest()
    cache = work / _OVERRIDE_CACHE_DIR / _safe_org(spec.get("org"))
    tree = cache / key
    if tree.exists():
        return tree
    for kind, sha in overrides.shas.items():
        if kind not in urls:
            raise RuntimeError(
                f"job needs {kind} artifact {sha}, but the lease signed no url for it"
            )
    cache.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cache, prefix=f".{key}.tmp-"))
    try:
        if isinstance(bundle, dict):
            # Copy the config's tree without the runs earlier jobs wrote into it: an override is a
            # local copy of the cached bundle, never a second download of it.
            shutil.copytree(
                base,
                tmp,
                dirs_exist_ok=True,
                ignore=lambda d, _names: [DEFAULT_RUNS_DIR] if Path(d) == base else [],
            )
        else:
            _materialize(tmp, materials)
        with tempfile.TemporaryDirectory(dir=cache, prefix=".fetch-") as raw:
            parts: dict[str, Path] = {}
            for kind, sha in overrides.shas.items():
                parts[kind] = Path(raw) / kind
                _fetch_part(urls[kind], parts[kind], sha)
            place_overrides(
                tmp, overrides.target, binary=parts.get("binary"), scenarios=parts.get("scenarios")
            )
        try:
            tmp.rename(tree)
        except OSError:
            # A concurrent build of the same key won the rename; its tree is equivalent, so drop ours.
            if not tree.exists():
                raise
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tree


def _bundle_workspace(work: Path, spec: dict[str, Any], bundle_urls: Any) -> Path:
    """The directory to run this job from: the uploaded bundle's root, or *work* when it ships none.

    A hosted `serve` binds an uploaded zip (or a composed triple) whose config names its `appPath`
    binary, its scenarios, and its baselines relative to the bundle root. The worker holds no project
    on disk, so it fetches what the lease signed and rebuilds that tree here. Running the job *from
    the bundle root* is what makes every one of those relative paths resolve, with no rewriting on
    either side.

    The tree is keyed by the bundle id, so a second job off the same bundle reuses it and fetches
    nothing. One tree therefore serves every job off one bundle, and each run writes its `runs/` and
    console log inside it — the worker already shares one working directory across jobs, and this
    keeps that property rather than paying a copy of an app binary per job.

    Raises rather than falling back to *work*: a run started against a missing binary fails opaquely
    at install time, far from this cause (directive 2).
    """
    bundle = spec.get("bundle")
    if not isinstance(bundle, dict):
        return work
    bundle_id = bundle.get("id")
    if not valid_sha256(bundle_id):
        # Server-authored, so this is purely defensive — but the id becomes a directory name below,
        # and a leased job spec is still remote input.
        raise RuntimeError(f"job carries an invalid bundle id: {bundle_id!r}")
    urls = bundle_urls if isinstance(bundle_urls, dict) else {}
    # Scoped per org, mirroring the control plane's own `_org_uploads_dir` / `_org_compositions_dir`.
    # A content-derived id makes a cross-org hit imply identical bytes, so what this buys is not
    # isolation of the bundle: the tree is *mutable* — each run writes its `runs/` inside it — and one
    # tenant's run evidence has no business landing in another tenant's directory.
    cache = work / _BUNDLE_CACHE_DIR / _safe_org(spec.get("org"))
    tree = cache / bundle_id
    if not tree.exists():
        if not urls:
            # The lease signed nothing: a control plane with no object store configured has nowhere
            # to have stored this bundle, so no worker can ever run the job. Say that here.
            raise RuntimeError(f"job needs bundle {bundle_id}, but the lease signed no url for it")
        tree = _fetch_bundle(cache, bundle_id, bundle, urls)
    config = find_bundle_config(tree)
    if config is None:
        raise RuntimeError(f"bundle {bundle_id} holds no bajutsu.config.yaml")
    return config.parent


def _expected_digest(bundle: dict[str, Any], name: str) -> str | None:
    """The sha256 the fetched *name* part must hash to, or None when the job names none for it.

    Both bind kinds hand over a real content digest: a single-zip bind's `id` *is* its zip's sha256,
    and a composed triple names one per leg. A composed bind's own `id` is a derived composition key
    with no single file behind it, so it is deliberately not used as a part digest here.
    """
    artifacts = bundle.get("artifacts")
    if artifacts is None:
        return bundle["id"] if name == "bundle" else None
    return artifacts.get(name) if isinstance(artifacts, dict) else None


def _fetch_part(url: str, dest: Path, expected_sha256: str | None) -> None:
    """Download one bundle part and prove it arrived whole, or raise `_TransientFetch`.

    The digest check is what makes a truncated download visible at all: a short body is *not* an
    error to `http.client`, which drops `IncompleteRead` on a `Content-Length` mismatch rather than
    raising, so `_get_file` returns happily with a partial file. Left unchecked, a truncated zip
    surfaces as a permanent "invalid bundle" — the network blip landing in the verdict by the other
    door — and a truncated raw binary (an `.ipa`, an `.apk`) is worse still: nothing downstream reads
    its bytes, so the corrupt tree is committed to the cache and every later job off that bundle
    reuses it without re-downloading.

    A `404`/`410` is re-raised untouched, so the caller reports it: the object is not there and no
    retry will conjure it.
    """
    try:
        _get_file(url, dest, timeout=_UPLOAD_HTTP_TIMEOUT)
    except HTTPError as e:
        if e.code in _GONE_STATUSES:
            raise
        raise _TransientFetch(f"HTTP {e.code} fetching {dest.name}") from e
    except (URLError, OSError) as e:
        raise _TransientFetch(f"could not fetch {dest.name}: {e}") from e
    if expected_sha256 is None:
        return
    digest = hashlib.sha256()
    with dest.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise _TransientFetch(
            f"{dest.name} did not match its digest (truncated or corrupted in transit)"
        )


def _fetch_bundle(
    cache: Path, bundle_id: str, bundle: dict[str, Any], urls: dict[str, Any]
) -> Path:
    """Download the bundle's stored objects and rebuild its tree at ``cache/<bundle_id>``.

    A single-zip bind arrives as one ``bundle`` zip and goes through `materialize_bundle`; a composed
    triple arrives as its legs and goes through `materialize_composition`. Both are the control
    plane's own functions, so the worker reproduces its tree by running that code rather than
    re-implementing it, and both apply `validate_bundle_config` — the check that confines every
    target's paths to the tree (BE-0051).

    The downloads land in a temporary directory that is removed either way: the rebuilt tree is what
    persists, and a half-fetched set of parts must not look like a cache entry.
    """
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix=".fetch-") as raw:
        parts: dict[str, Path] = {}
        for name, url in urls.items():
            # Control-plane-authored names, re-checked because a lease response is remote input and
            # each one is joined onto a path below.
            if name not in _BUNDLE_PART_NAMES or not isinstance(url, str):
                raise RuntimeError(f"unexpected bundle part {name!r} in the lease response")
            parts[name] = Path(raw) / name
            _fetch_part(url, parts[name], _expected_digest(bundle, name))
        if bundle.get("artifacts") is None:
            if "bundle" not in parts:
                raise RuntimeError(f"bundle {bundle_id} was signed with no zip to fetch")
            return materialize_bundle(
                parts["bundle"], cache, bundle_id, validate=validate_bundle_config
            )
        if "config" not in parts:
            raise RuntimeError(f"composed bundle {bundle_id} was signed with no config artifact")
        return materialize_composition(
            parts["config"],
            parts.get("scenarios"),
            parts.get("binary"),
            compositions_dir=cache,
            composition_id=bundle_id,
            scenarios_filename=bundle.get("scenarios_filename"),
        )


def _download_baselines(work: Path, baseline_urls: dict[str, Any]) -> None:
    """Download each ``name -> presigned GET url`` baseline into ``work/baselines`` before the run.

    The dir is cleared first — the workspace is reused across jobs, so a baseline renamed/removed in
    storage must not linger and skew the comparison — and each name is confined under it. A download
    failure raises: a run that silently dropped its visual baselines would compare against nothing.
    """
    baselines = work / "baselines"
    if baselines.exists():
        shutil.rmtree(baselines, ignore_errors=True)
    if not baseline_urls:
        return
    base = baselines.resolve()
    for name, get_url in baseline_urls.items():
        # The control plane signs only safe baseline names, so a non-string name/URL or an escaping
        # name is a broken/hostile lease: fail loudly rather than silently drop a baseline (which
        # would leave the run comparing against nothing). Validate the types before the path-join so
        # a bad name raises this RuntimeError, not a TypeError, and never place a file outside the dir.
        if not isinstance(name, str) or not isinstance(get_url, str):
            raise RuntimeError(f"baseline {name!r} has a non-string name or URL")  # noqa: TRY004  # invalid external payload, not a caller type error
        dest = (baselines / name).resolve()
        if base not in dest.parents:
            raise RuntimeError(f"baseline {name!r} escapes the baselines dir")
        dest.parent.mkdir(parents=True, exist_ok=True)
        _get_file(get_url, dest, timeout=_UPLOAD_HTTP_TIMEOUT)


class PresignedWorkerIO:
    """The worker's object I/O over the control plane's presigned URLs (BE-0160), the `WorkerIO` seam.

    Holds no cloud credentials — only the control-plane URL, the operator token, the leased job id,
    and the signed baseline GET URLs the lease returned. The org is fixed server-side from the leased
    job, so this can never touch another tenant's prefix. Uploads fail loudly (they feed the report),
    unlike the best-effort post-verdict evidence upload.
    """

    def __init__(
        self, *, url: str, auth_token: str | None, job_id: str, worker_id: str, baseline_urls: Any
    ) -> None:
        self._url = url
        self._token = auth_token
        self._job_id = job_id
        self._worker_id = worker_id
        self._baseline_urls = baseline_urls if isinstance(baseline_urls, dict) else {}

    def download_baselines(self, work: Path) -> None:
        _download_baselines(work, self._baseline_urls)

    def upload_run(self, work: Path, run_id: str) -> None:
        run_dir = work / DEFAULT_RUNS_DIR / run_id
        if not run_dir.is_dir():
            return
        files = _evidence_files(run_dir)
        if not files:
            return
        urls = _request_upload_urls(
            self._url,
            "/api/worker/artifact-urls",
            {
                "job_id": self._job_id,
                "worker_id": self._worker_id,
                "run_id": run_id,
                "files": files,
            },
            self._token,
        )
        _put_tree_files(run_dir, urls, best_effort=False)

    def save_scenario(self, work: Path, out_path: str, app: str, ref: str) -> None:
        src = (work / out_path).resolve()
        # Confine to the workspace: a crafted spec with an absolute / `..` out_path must not read &
        # upload a host file outside it (the control plane never builds such a path).
        if work.resolve() not in src.parents:
            return
        # A `record` job that reached here was expected to author a scenario; if the file is missing,
        # fail loudly rather than report success having persisted nothing (BE-0160 / fail loud).
        if not src.is_file():
            raise RuntimeError(f"record job authored no scenario at {out_path!r}")
        code, resp = _post_json(
            f"{self._url}/api/worker/scenario-url",
            {"job_id": self._job_id, "worker_id": self._worker_id, "app": app, "ref": ref},
            token=self._token,
            timeout=_UPLOAD_HTTP_TIMEOUT,
        )
        if code != 200:
            raise RuntimeError(f"scenario-url returned {code}")
        put_url = resp.get("url")
        if not isinstance(put_url, str):
            raise RuntimeError("scenario-url returned no URL")  # noqa: TRY004  # invalid external payload, not a caller type error
        _put_file(put_url, src, content_type_for(ref), timeout=_UPLOAD_HTTP_TIMEOUT)


def _upload_evidence(
    work: Path, run_id: str, *, url: str, auth_token: str | None, evidence_prefix: str
) -> None:
    """Ask the control plane for presigned PUT URLs for this run's tree and upload each file.

    Best-effort by design (BE-0110): the run's result is already posted, so any failure here is
    logged and dropped, never raised, and every HTTP call is time-bounded so a stall can't strand the
    worker. When no evidence store is configured the endpoint returns no URLs and this uploads nothing.
    """
    run_dir = work / DEFAULT_RUNS_DIR / run_id
    if not run_dir.is_dir():
        return
    files = _evidence_files(run_dir)
    if not files:
        return
    try:
        urls = _request_upload_urls(
            url,
            f"/api/runs/{run_id}/upload-urls",
            {"files": files, "evidence_prefix": evidence_prefix},
            auth_token,
        )
    except Exception as e:
        _logger.warning("evidence upload-urls failed for run %s: %s", run_id, e)
        return
    uploaded = _put_tree_files(run_dir, urls, best_effort=True)
    if uploaded:
        typer.echo(f"  uploaded {uploaded} evidence file(s) for run {run_id}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(worker)
