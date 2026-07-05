"""Bajutsu CLI. Per-app differences come from config; the runner is shared.

Each command lives in its own `commands/<name>.py` module that exposes a
`register(app)` function; the directory scan below assembles them into the Typer
app. **Adding a command is adding a file** — drop a `commands/<name>.py` and it is
picked up automatically, with no edit to this file (BE-0043: new work adds files
instead of editing a shared monolith).
"""

from __future__ import annotations

import importlib
import pkgutil

import typer

from bajutsu import capabilities
from bajutsu.cli import commands
from bajutsu.dotenv import load_dotenv

# Rich help panels that split `bajutsu --help` on the Claude boundary (BE-0101), so the split is the
# first thing `--help` shows. Titles are the two buckets `capabilities` classifies into.
_CLAUDE_FREE_PANEL = "Claude-free (zero-config)"
_CLAUDE_USING_PANEL = "Uses Claude"

app = typer.Typer(
    add_completion=False,
    help="自然言語駆動の E2E テストツール",
)


@app.callback()
def _bootstrap() -> None:
    """Load a gitignored .env (e.g. ANTHROPIC_API_KEY) before any command runs."""
    load_dotenv()


def _register_commands() -> None:
    """Import every `commands/<name>.py` and let each register its command(s) onto `app`.

    Sorted for a stable --help order.
    """
    for name in sorted(mod.name for mod in pkgutil.iter_modules(commands.__path__)):
        module = importlib.import_module(f"{commands.__name__}.{name}")
        module.register(app)


def _group_by_claude_use() -> None:
    """Sort each command into the Claude-free / uses-Claude help panel from `capabilities` (BE-0101).

    Done once here rather than in each `commands/<name>.py` so the classification stays in one place
    and adding a command needs no help-panel wiring — its `capabilities` entry drives the panel.
    """
    for info in app.registered_commands:
        name = info.name or (info.callback.__name__.replace("_", "-") if info.callback else "")
        cap = capabilities.by_command(name)
        if cap is not None:
            info.rich_help_panel = _CLAUDE_USING_PANEL if cap.uses_claude else _CLAUDE_FREE_PANEL


_register_commands()
_group_by_claude_use()

__all__ = ["app"]


if __name__ == "__main__":
    app()
