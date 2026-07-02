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
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import typer

from bajutsu import env
from bajutsu.serve import InMemoryLogBus
from bajutsu.serve.server.worker_job import execute_job_spec

_logger = logging.getLogger("bajutsu.worker")


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
    worker_id: str = typer.Option("", "--worker-id", help="Worker identifier"),
) -> None:
    """Run a worker that leases queued `bajutsu run` jobs from the control plane over HTTP.

    Polls POST /api/worker/lease; on a job, runs execute_job_spec, uploads the run tree, and
    posts the result to POST /api/worker/result.
    """
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

        store = _object_store()
        bus = InMemoryLogBus()
        try:
            job = execute_job_spec(
                spec,
                popen=subprocess.Popen,
                simctl=env._real_run,
                cwd=work,
                bus=bus,
                store=store,
            )
            result = job.view()
            result.pop("lines", None)
        except Exception as e:
            _logger.exception("job %s failed", job_id)
            result = {"ok": False, "error": str(e)}

        run_id = result.get("runId")
        if run_id:
            _write_console_log(work, run_id, bus, job_id)

        try:
            _post_json(
                f"{url}/api/worker/result",
                {"job_id": job_id, "result": result},
                token=auth_token,
            )
        except (URLError, OSError) as e:
            _logger.error("result post failed for job %s: %s", job_id, e)

        typer.echo(f"  completed job {job_id}")


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


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(worker)
