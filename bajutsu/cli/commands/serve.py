"""`bajutsu serve` — launch the local web UI (Tier 1; not for CI)."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

import typer


def _is_loopback(host: str) -> bool:
    """Whether binding `host` keeps the server private to this machine.

    Uses real loopback semantics (so `127.0.0.2`, `::1` in any form, etc. all count), plus the
    `localhost` name.
    """
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
    emit_launchagent: bool = typer.Option(
        False,
        "--emit-launchagent",
        help="print a launchd LaunchAgent plist for these flags and exit (self-hosting, BE-0016)",
    ),
    asgi: bool = typer.Option(
        False,
        "--asgi",
        help="serve the FastAPI app over uvicorn instead of the stdlib server (needs bajutsu[server])",
    ),
    backend: str = typer.Option(
        "local",
        "--backend",
        help="which serve seams to assemble (only 'local' available; hosted backends land later)",
    ),
) -> None:
    """Launch a local web UI to run scenarios and view their reports (Tier 1; not for CI).

    Without `--config`, open a config.yml from the UI's file browser (limited to `--root`).
    With `--token` (or $BAJUTSU_SERVE_TOKEN) every request must authenticate; binding a
    non-loopback `--host` requires one so the server is never exposed unauthenticated.
    With `--emit-launchagent`, print a LaunchAgent plist matching these flags (for self-hosting)
    and exit without starting the server. With `--asgi`, serve the same UI/API as a FastAPI app
    over uvicorn (the transport the hosted backend will use); `--backend` selects which seams to
    assemble (only `local` for now).
    """
    from bajutsu.serve import SERVE_BACKENDS, MissingServerExtra, launchagent_plist
    from bajutsu.serve import serve as _serve

    resolved_token = token or os.environ.get("BAJUTSU_SERVE_TOKEN") or ""
    if not _is_loopback(host) and not resolved_token:
        typer.echo(
            f"refusing to bind non-loopback host {host!r} without a token — "
            "pass --token or set BAJUTSU_SERVE_TOKEN"
        )
        raise typer.Exit(2)

    if backend not in SERVE_BACKENDS:
        typer.echo(f"unknown --backend {backend!r} (available: {', '.join(SERVE_BACKENDS)})")
        raise typer.Exit(2)

    # `--asgi` needs the optional `server` extra (FastAPI + uvicorn); fail with an install hint
    # rather than a raw ImportError traceback, mirroring `bajutsu worker`.
    if asgi:
        try:
            import uvicorn  # noqa: F401
        except ImportError:
            typer.echo(
                "the `server` extra is required for --asgi — "
                "install with: pip install 'bajutsu[server]'"
            )
            raise typer.Exit(2) from None

    if emit_launchagent:
        typer.echo(
            launchagent_plist(
                host=host, port=port, config=config or None, token=resolved_token or None
            )
        )
        return

    try:
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
            asgi=asgi,
            backend=backend,
        )
    except MissingServerExtra as e:
        # The server backend was selected without its optional extras: show the install hint and
        # exit cleanly rather than dumping a traceback (mirrors worker). Only this specific error is
        # caught — a plain ImportError (e.g. a real internal bug) keeps its traceback.
        typer.echo(str(e))
        raise typer.Exit(2) from None


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(serve)
