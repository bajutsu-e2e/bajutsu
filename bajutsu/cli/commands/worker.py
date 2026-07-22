"""`bajutsu worker` — lease queued runs from the control plane and execute them (BE-0106).

The hosted control plane (`serve --backend=server`) inserts a job row per run; this command polls
the `/api/worker/lease` endpoint over HTTP, executes the unchanged `run_job`, uploads the run tree
(including `console.log`), and posts the result back to `/api/worker/result`. No Redis or RQ, and
**no cloud credentials** (BE-0160): every object-store touch — downloading baselines before a run,
uploading the run tree and a `record` job's authored scenario after — goes through presigned URLs
the control plane signs, so the worker needs only an HTTP client.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer

from bajutsu import simctl
from bajutsu.backends import PLATFORMS
from bajutsu.object_store import content_type_for
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.capabilities import WORKER_CAPABILITIES_ENV, worker_capabilities
from bajutsu.serve.server.worker_job import WorkerIO, execute_job_spec

_logger = logging.getLogger("bajutsu.worker")

# Heartbeat well under the control plane's default lease timeout (DEFAULT_LEASE_TIMEOUT_SECONDS) so
# a legitimately long run is never mistaken for a dead worker and reclaimed (BE-0016).
DEFAULT_HEARTBEAT_INTERVAL = 30.0

# Per-request timeout for the presigned upload/download paths (BE-0110/BE-0160), so a stalled
# connection can't hang the worker on a single file (evidence upload runs after heartbeats stop).
_UPLOAD_HTTP_TIMEOUT = 60.0


def _post_json(
    url: str, body: dict[str, Any], *, token: str | None = None, timeout: float | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=data, headers=headers)  # noqa: S310
    try:
        with urlopen(req, timeout=timeout) as r:  # noqa: S310
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read() if e.fp else b""
        return e.code, json.loads(raw) if raw else {}


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
            run=simctl._real_run if "ios" in platforms else None,
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
        result, abandoned = _run_with_heartbeat(
            spec,
            job_id=job_id,
            work=work,
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

        run_id = result.get("runId")
        if run_id:
            _write_console_log(work, run_id, bus, job_id)

        try:
            _post_json(
                f"{url}/api/worker/result",
                {"job_id": job_id, "result": result, "worker_id": wid},
                token=auth_token,
            )
        except (URLError, OSError) as e:
            _logger.error("result post failed for job %s: %s", job_id, e)

        # Upload the run's evidence via presigned URLs the control plane signs (BE-0110): the worker
        # holds no cloud credentials of its own. Runs *after* the result is posted — heartbeats stop
        # once the run returns, so a slow upload must not delay the post and risk the lease being
        # reclaimed. Best-effort and time-bounded: a failure or stall warns and never affects the run.
        if run_id:
            _upload_evidence(
                work,
                run_id,
                url=url,
                auth_token=auth_token,
                evidence_prefix=str(spec.get("evidence_prefix") or ""),
            )

        typer.echo(f"  completed job {job_id}")


def _run_with_heartbeat(
    spec: dict[str, Any],
    *,
    job_id: str,
    work: Path,
    bus: InMemoryLogBus,
    url: str,
    wid: str,
    auth_token: str | None,
    heartbeat_interval: float,
    io: WorkerIO | None = None,
) -> tuple[dict[str, Any], bool]:
    """Run the job on a background thread while heart-beating its lease from this one.

    Object I/O (baseline download, run-tree/scenario upload) runs on that thread through *io*, so the
    heartbeat keeps the lease alive while a large artifact uploads. Returns ``(result, abandoned)``;
    *abandoned* is True when the control plane reclaimed the lease mid-run (HTTP 409), meaning another
    worker now owns the job and this result should be dropped.
    """
    holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            job = execute_job_spec(
                spec,
                popen=subprocess.Popen,
                simctl=simctl._real_run,
                cwd=work,
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
            )
        except (URLError, OSError) as e:
            _logger.warning("heartbeat failed for job %s: %s", job_id, e)
            continue
        if code == 409:
            abandoned = True
            runner.join()  # wait it out so this worker never runs two jobs at once
            break

    return holder.get("result", {"ok": False, "error": "worker produced no result"}), abandoned


def _write_console_log(work: Path, run_id: str, bus: InMemoryLogBus, job_id: str) -> None:
    """Write the job's buffered log to runs/<run_id>/console.log for upload."""
    run_dir = work / "runs" / run_id
    if not run_dir.is_dir():
        return
    lines = list(bus.stream(job_id, timeout=0.0))
    if not lines:
        return
    (run_dir / "console.log").write_text(
        "".join(line for line in lines if line is not None),
        encoding="utf-8",
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
    headers = {"Content-Length": str(path.stat().st_size)}
    if content_type:
        headers["Content-Type"] = content_type
    with path.open("rb") as body:
        req = Request(url, data=body, method="PUT", headers=headers)  # noqa: S310
        with urlopen(req, timeout=timeout):  # noqa: S310
            pass


def _get_file(url: str, dest: Path, *, timeout: float | None = None) -> None:
    """Download a presigned GET *url* into *dest* (read into memory — baselines are small images)."""
    req = Request(url, method="GET")  # noqa: S310
    with urlopen(req, timeout=timeout) as r:  # noqa: S310
        dest.write_bytes(r.read())


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
        raise RuntimeError(f"{endpoint} returned an unexpected response shape")
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
            raise RuntimeError(f"baseline {name!r} has a non-string name or URL")
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
        run_dir = work / "runs" / run_id
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
            raise RuntimeError("scenario-url returned no URL")
        _put_file(put_url, src, content_type_for(ref), timeout=_UPLOAD_HTTP_TIMEOUT)


def _upload_evidence(
    work: Path, run_id: str, *, url: str, auth_token: str | None, evidence_prefix: str
) -> None:
    """Ask the control plane for presigned PUT URLs for this run's tree and upload each file.

    Best-effort by design (BE-0110): the run's result is already posted, so any failure here is
    logged and dropped, never raised, and every HTTP call is time-bounded so a stall can't strand the
    worker. When no evidence store is configured the endpoint returns no URLs and this uploads nothing.
    """
    run_dir = work / "runs" / run_id
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
