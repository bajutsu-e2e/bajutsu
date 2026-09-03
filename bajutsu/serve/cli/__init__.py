"""The `serve`/`worker`/`approve` Typer commands, kept as separate files (they were three
separate `cli/commands/*.py` files before BE-0257's follow-on feature-colocation and stay that
way rather than merging into one blob) with this module as the thin mount point `bajutsu.cli`
calls into.
"""

from __future__ import annotations

import typer

from bajutsu.serve.cli import approve, serve, worker


def register(app: typer.Typer) -> None:
    """Register `serve`, `worker`, and `approve` on the Typer app."""
    serve.register(app)
    worker.register(app)
    approve.register(app)
