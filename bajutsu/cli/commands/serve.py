"""`bajutsu serve` — launch the local web UI (Tier 1; not for CI)."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import typer


def _is_loopback(host: str) -> bool:
    """Whether binding `host` keeps the server private to this machine. Uses real loopback
    semantics (so `127.0.0.2`, `::1` in any form, etc. all count), plus the `localhost` name."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a hostname we can't classify — treat as non-loopback (needs a token)


def serve(
    port: int = typer.Option(8765, "--port"),
    config: str = typer.Option(
        "", "--config", help="config to bind at startup; omit to open one from the UI"
    ),
    root: str = typer.Option(
        "", "--root", help="root the UI's file browser may explore (default: current directory)"
    ),
    scenarios: str = typer.Option(
        "", "--scenarios", help="override the app's scenarios dir (default: from config)"
    ),
    runs: str = typer.Option("runs", "--runs", help="runs root to serve reports from"),
    baselines: str = typer.Option(
        "",
        "--baselines",
        help="visual-regression baselines dir (default: a `baselines` folder under --scenarios)",
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    max_concurrent_runs: int = typer.Option(
        4,
        "--max-concurrent-runs",
        help="cap on concurrently-running run/record jobs (0 = unlimited); over the cap returns 429",
    ),
    token: str = typer.Option(
        "",
        "--token",
        help="shared token required for every request (or set BAJUTSU_SERVE_TOKEN). "
        "Required to bind a non-loopback --host.",
    ),
) -> None:
    """Launch a local web UI to run scenarios and view their reports (Tier 1; not for CI).

    Without `--config`, open a config.yml from the UI's file browser (limited to `--root`).
    With `--token` (or $BAJUTSU_SERVE_TOKEN) every request must authenticate; binding a
    non-loopback `--host` requires one so the server is never exposed unauthenticated."""
    from bajutsu.serve import serve as _serve

    resolved_token = token or os.environ.get("BAJUTSU_SERVE_TOKEN") or ""
    if not _is_loopback(host) and not resolved_token:
        typer.echo(
            f"refusing to bind non-loopback host {host!r} without a token — "
            "pass --token or set BAJUTSU_SERVE_TOKEN"
        )
        raise typer.Exit(2)

    _serve(
        host=host,
        port=port,
        scenarios_dir=Path(scenarios) if scenarios else None,
        config=Path(config) if config else None,
        runs_dir=Path(runs),
        root=Path(root) if root else Path.cwd(),
        baselines_dir=Path(baselines) if baselines else None,
        max_concurrent=max_concurrent_runs,
        token=resolved_token or None,
    )


def register(app: typer.Typer) -> None:
    app.command()(serve)
