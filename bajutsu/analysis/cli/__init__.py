"""The `audit`/`coverage`/`impact`/`stats`/`flakiness`/`export`/`trace` Typer commands, kept as
separate files (one per `cli/commands/*.py` file before feature-colocation) with this module as
the thin mount point `bajutsu.cli` calls into.
"""

from __future__ import annotations

import typer

from bajutsu.analysis.cli import audit, coverage, export, flakiness, impact, stats, trace


def register(app: typer.Typer) -> None:
    """Register `audit`, `coverage`, `impact`, `stats`, `flakiness`, `export`, and `trace`."""
    audit.register(app)
    coverage.register(app)
    impact.register(app)
    stats.register(app)
    flakiness.register(app)
    export.register(app)
    trace.register(app)
