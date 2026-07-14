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
    themes: str = typer.Option(
        "",
        "--themes",
        help="drop-in theme dir: each *.css adds a selectable UI theme (BE-0191); "
        "the built-in dark/light pair is always offered",
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
    upload_exec: str = typer.Option(
        "",
        "--upload-exec",
        help="policy for an uploaded bundle's launchServer command (BE-0090): "
        "deny | reuse | sandbox (default sandbox; or set BAJUTSU_UPLOAD_EXEC). A local/Git config "
        "is operator-trusted and unaffected.",
    ),
    evidence_store: str = typer.Option(
        "",
        "--evidence-store",
        envvar="BAJUTSU_EVIDENCE_STORE",
        help="upload each completed run's evidence to object storage at this URI "
        "(s3://bucket/prefix or gs://bucket/prefix); the upload path picks the cloud lifecycle "
        "policy. The server holds the credentials and hands workers presigned PUT URLs, so a "
        "worker uploads without any cloud credentials of its own (BE-0110). Needs the s3 or gcs extra",
    ),
    allow_remote_build: bool = typer.Option(
        False,
        "--allow-remote-build",
        envvar="BAJUTSU_ALLOW_REMOTE_BUILD",
        help="run the `build:` command of a Git config bound at runtime through the UI (BE-0121). "
        "Off by default: an API-bound Git config is untrusted, so its build is never run on the "
        "host unless you opt in here. A local/startup config is operator-trusted and unaffected.",
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

    # The flag wins, then the env mirror (for the hosted backend), then the safe-yes default.
    resolved_upload_exec = upload_exec or os.environ.get("BAJUTSU_UPLOAD_EXEC") or "sandbox"
    if resolved_upload_exec not in ("deny", "reuse", "sandbox"):
        typer.echo(f"unknown --upload-exec {resolved_upload_exec!r} (choose: deny, reuse, sandbox)")
        raise typer.Exit(2)

    # Resolve --evidence-store to a credentialed store now (BE-0110), failing fast with a clean hint
    # — not a traceback — on a malformed URI or a missing cloud SDK, mirroring the --asgi / --config
    # handling below. The server holds the credentials; workers upload via presigned PUT URLs.
    evidence = None
    if evidence_store:
        from bajutsu.object_store import evidence_target_from_uri

        try:
            evidence = evidence_target_from_uri(evidence_store)
        except (ValueError, ImportError) as e:
            typer.echo(f"--evidence-store {evidence_store}: {e}")
            raise typer.Exit(2) from None

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
                host=host,
                port=port,
                config=config or None,
                token=resolved_token or None,
                upload_exec=resolved_upload_exec,
            )
        )
        return

    # `--config github:…` binds a Git source at startup (BE-0063), the same way a local path does:
    # materialize the checkout and serve from its root, so the config's relative scenarios/build/
    # baselines resolve against the fetched tree. A non-spec value stays a local path.
    config_path = Path(config) if config else None
    cwd: Path | None = None
    config_provenance: dict[str, str] | None = None
    if config:
        from bajutsu.config_source import materialize, parse_config_spec, source_provenance

        spec = parse_config_spec(config)
        if spec is not None:
            try:
                mat = materialize(spec)
            except (OSError, ValueError) as e:
                typer.echo(f"--config {config}: {e}")
                raise typer.Exit(2) from None
            config_path, cwd = mat.config_path, mat.root
            # Stamp the resolved commit so the UI's "view config" can show which commit this opaque
            # cache-path config was materialized from, not just the path (BE-0063).
            config_provenance = source_provenance(spec, mat)
        else:
            # A local config's relative paths resolve from its own directory, so the served config
            # behaves the same wherever serve was started, matching the CLI and the Git bind (BE-0242).
            # Resolve config_path itself too — a run job passes it as `--config` to a subprocess
            # launched with cwd=cwd (the config's directory); left relative to the original launch
            # cwd, that argument would no longer resolve once cwd moves.
            assert config_path is not None  # set above from a truthy `config`
            config_path = config_path.resolve()
            cwd = config_path.parent

    # The initial theme selection is a serve-only `ui.default_theme` key, read from the startup
    # config here (the core Config never models it — BE-0191). None follows the OS as before.
    from bajutsu.serve.themes import read_default_theme

    default_theme = read_default_theme(config_path)

    try:
        _serve(
            host=host,
            port=port,
            scenarios_dir=Path(scenarios) if scenarios else None,
            config=config_path,
            runs_dir=Path(runs),
            root=Path(root) if root else Path.cwd(),
            baselines_dir=Path(baselines) if baselines else None,
            max_concurrent=max_concurrent_runs,
            token=resolved_token or None,
            upload_exec=resolved_upload_exec,
            evidence=evidence,
            allow_remote_build=allow_remote_build,
            asgi=asgi,
            backend=backend,
            cwd=cwd,
            config_provenance=config_provenance,
            themes_dir=Path(themes) if themes else None,
            default_theme=default_theme,
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
