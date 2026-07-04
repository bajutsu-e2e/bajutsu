"""`bajutsu worker` — lease queued runs from the control plane and execute them (BE-0106).

The hosted control plane (`serve --backend=server`) inserts a job row per run; this command polls
the `/api/worker/lease` endpoint over HTTP, executes the unchanged `run_job`, uploads the run tree
(including `console.log`), and posts the result back to `/api/worker/result`. No Redis or RQ —
the worker needs only an HTTP client and (optionally) an object-store client.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer

from bajutsu import env
from bajutsu.object_store import content_type_for
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.server.worker_job import execute_job_spec

_logger = logging.getLogger("bajutsu.worker")

# Heartbeat well under the control plane's default lease timeout (DEFAULT_LEASE_TIMEOUT_SECONDS) so
# a legitimately long run is never mistaken for a dead worker and reclaimed (BE-0016).
DEFAULT_HEARTBEAT_INTERVAL = 30.0


def _post_json(url: str, body: dict[str, Any], *, token: str | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=data, headers=headers)  # noqa: S310
    try:
        with urlopen(req) as r:  # noqa: S310
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except HTTPError as e:
        raw = e.read() if e.fp else b""
        return e.code, json.loads(raw) if raw else {}


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

    typer.echo(f"bajutsu worker → polling {url}  (Ctrl-C to stop)")
    while True:
        try:
            code, body = _post_json(
                f"{url}/api/worker/lease",
                {"worker_id": wid},
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
        )
        if abandoned:
            # The control plane reclaimed and likely re-leased this job to another worker; posting a
            # result would race that worker, so drop it (the re-run is the source of truth).
            typer.echo(f"  lease lost for job {job_id}; abandoning")
            continue

        run_id = result.get("runId")
        if run_id:
            _write_console_log(work, run_id, bus, job_id)
            # Upload the run's evidence via presigned URLs the control plane signs (BE-0110): the
            # worker holds no cloud credentials of its own. Best-effort — a failure warns and never
            # touches the result. After console.log so it uploads too.
            _upload_evidence(
                work,
                run_id,
                url=url,
                auth_token=auth_token,
                evidence_prefix=str(spec.get("evidence_prefix") or ""),
            )

        try:
            _post_json(
                f"{url}/api/worker/result",
                {"job_id": job_id, "result": result, "worker_id": wid},
                token=auth_token,
            )
        except (URLError, OSError) as e:
            _logger.error("result post failed for job %s: %s", job_id, e)

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
) -> tuple[dict[str, Any], bool]:
    """Run the job on a background thread while heart-beating its lease from this one.

    Returns ``(result, abandoned)``; *abandoned* is True when the control plane reclaimed the lease
    mid-run (HTTP 409), meaning another worker now owns the job and this result should be dropped.
    """
    holder: dict[str, Any] = {}

    def _run() -> None:
        try:
            job = execute_job_spec(
                spec,
                popen=subprocess.Popen,
                simctl=env._real_run,
                cwd=work,
                bus=bus,
                store=_object_store(),
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


def _object_store() -> Any:
    try:
        from bajutsu.serve.server.object_store import object_store_from_env

        return object_store_from_env()
    except ImportError:
        return None


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
    """Relative POSIX paths of every real file under *run_dir* (the keys the endpoint signs).

    Symlinks and non-files are skipped, and each resolved path must stay under the run dir, so
    nothing outside the tree is offered for upload (mirrors `_upload_runs`).
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


def _put_file(url: str, path: Path, content_type: str) -> None:
    """Upload one file to a presigned PUT *url*.

    The Content-Type must match what the control plane signed into the URL (the presigned signature
    covers it), so send the same value.
    """
    headers = {"Content-Type": content_type} if content_type else {}
    req = Request(url, data=path.read_bytes(), method="PUT", headers=headers)  # noqa: S310
    with urlopen(req):  # noqa: S310
        pass


def _upload_evidence(
    work: Path, run_id: str, *, url: str, auth_token: str | None, evidence_prefix: str
) -> None:
    """Ask the control plane for presigned PUT URLs for this run's tree and upload each file.

    Best-effort by design (BE-0110): the verdict is already final and the result post still to come
    must not be skipped, so any failure here is logged and dropped, never raised (a broad catch, like
    `upload_tree`, so even a malformed URL can't crash the worker). When no evidence store is
    configured the endpoint returns no URLs and this uploads nothing.
    """
    run_dir = work / "runs" / run_id
    if not run_dir.is_dir():
        return
    files = _evidence_files(run_dir)
    if not files:
        return
    try:
        code, body = _post_json(
            f"{url}/api/runs/{run_id}/upload-urls",
            {"files": files, "evidence_prefix": evidence_prefix},
            token=auth_token,
        )
    except Exception as e:
        _logger.warning("evidence upload-urls request failed for run %s: %s", run_id, e)
        return
    if code != 200:
        _logger.warning("evidence upload-urls returned %s for run %s", code, run_id)
        return
    urls = body.get("urls")
    if not isinstance(urls, dict):  # a malformed response must not crash the worker
        _logger.warning("evidence upload-urls returned an unexpected shape for run %s", run_id)
        return
    base = run_dir.resolve()
    uploaded = 0
    for rel, put_url in urls.items():
        # Confine each returned key under the run dir and require a string URL, so a malformed or
        # hostile response can neither read files outside the tree nor crash the loop.
        src = (run_dir / rel).resolve()
        if not isinstance(put_url, str) or not src.is_relative_to(base):
            _logger.warning("skipping unexpected upload entry %r for run %s", rel, run_id)
            continue
        try:
            _put_file(put_url, src, content_type_for(rel))
        except Exception as e:
            _logger.warning("evidence upload failed for %s: %s", rel, e)
        else:
            uploaded += 1
    if uploaded:
        typer.echo(f"  uploaded {uploaded} evidence file(s) for run {run_id}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(worker)
