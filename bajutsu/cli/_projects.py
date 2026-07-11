"""Shared helpers for the project-hub CLI (BE-0225 unit 5).

The `project` subcommands and `run --project` both read the same on-disk project store `serve`
exposes over HTTP, so opening it lives here — cross-command, so it stays out of any one
`commands/<name>.py`. The `--config`-spec ↔ config-source-record conversion (`source_from_config` /
`config_from_source`) is a core concern shared with `serve`'s own project rebind (unit 4), so it
lives in `bajutsu.config_source` beside `parse_config_spec` and is re-exported here for the CLI's
existing call sites.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.config_source import config_from_source, source_from_config

if TYPE_CHECKING:
    from bajutsu.serve.project_registry import ProjectRegistry

__all__ = ["config_from_source", "open_registry", "source_from_config"]


def open_registry(runs: str) -> ProjectRegistry:
    """The registry the CLI edits, sharing `serve`'s store.

    The DB `Repository` when `BAJUTSU_DATABASE_URL` is set, else the on-disk JSON store beside *runs*
    (the same store a local `serve` uses). Imported lazily to keep `bajutsu --help` off the serve
    import path.

    The CLI is local-first: unlike the API's `register_project`, it does not apply the deployment
    source-kind allowlist (BE-0108). That allowlist is `serve`-state policy guarding a browser
    client from the server's filesystem, whereas `file` is the CLI's primary source and its operator
    already has that access — so pointing the CLI at a hosted DB is an operator responsibility, not a
    screened path.
    """
    from bajutsu.serve.project_registry import LocalProjectRegistry, SqlProjectRegistry
    from bajutsu.serve.server.db import repository_from_env

    repository = repository_from_env()
    if repository is not None:
        return SqlProjectRegistry(repository)
    return LocalProjectRegistry(Path(runs).parent / "projects.json")
