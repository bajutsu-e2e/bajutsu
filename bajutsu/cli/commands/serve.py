"""`bajutsu serve` — launch the local web UI (Tier 1; not for CI)."""

from __future__ import annotations

import os
from pathlib import Path

import typer

# Hosts that keep the server private to this machine; anything else needs a token (BE-0051).
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


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
    if host not in _LOOPBACK_HOSTS and not resolved_token:
        typer.echo(
            f"refusing to bind non-loopback host {host!r} without a token — "
            "pass --token or set BAJUTSU_SERVE_TOKEN"
        )
        raise typer.Exit(2)

    _serve(
        host,
        port,
        Path(scenarios) if scenarios else None,
        Path(config) if config else None,
        Path(runs),
        Path(root) if root else Path.cwd(),
        Path(baselines) if baselines else None,
        resolved_token or None,
    )


def register(app: typer.Typer) -> None:
    app.command()(serve)
