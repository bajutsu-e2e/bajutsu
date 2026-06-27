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

from bajutsu.cli import commands
from bajutsu.dotenv import load_dotenv

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


_register_commands()

__all__ = ["app"]


if __name__ == "__main__":
    app()
