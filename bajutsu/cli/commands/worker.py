"""`bajutsu worker` — lease queued runs from Redis and execute them (BE-0015 server phase).

The hosted control plane (`serve --backend=server`) enqueues a job per run; this command consumes
them on a host that has a Simulator and runs the unchanged `run_job`. RQ/Redis are imported lazily
**inside the command** (they live in the ``worker`` optional-dependency group), so importing the
CLI never pulls them in — the default path stays server-free (`tests/serve/test_import_guard.py`).
"""

from __future__ import annotations

import os

import typer


def worker(
    redis_url: str = typer.Option(
        "", "--redis-url", help="Redis URL (default: $BAJUTSU_REDIS_URL / $REDIS_URL / localhost)"
    ),
    queue: str = typer.Option("bajutsu", "--queue", help="queue name to consume"),
) -> None:
    """Run a worker that leases queued `bajutsu run` jobs from Redis and executes them.

    Requires the `worker` extra (`pip install 'bajutsu[worker]'`): RQ + Redis. The enqueued spec
    carries only the job's argv/devices/build (see `bajutsu.serve.server.worker_job`); this process
    supplies the real subprocess and Simulator."""
    url = (
        redis_url
        or os.environ.get("BAJUTSU_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379"
    )
    try:
        from redis import Redis
        from rq import Queue, Worker
    except ImportError as e:
        typer.echo("the `worker` extra is required — install with: pip install 'bajutsu[worker]'")
        raise typer.Exit(2) from e

    connection = Redis.from_url(url)
    typer.echo(f"bajutsu worker → consuming '{queue}' from {url}  (Ctrl-C to stop)")
    Worker([Queue(queue, connection=connection)], connection=connection).work()


def register(app: typer.Typer) -> None:
    app.command()(worker)
