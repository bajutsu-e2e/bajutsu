"""Bajutsu CLI. Per-app differences come from config; the runner is shared.

Each command's Typer `register(app)` lives with its owning feature (`bajutsu.run.cli`,
`bajutsu.record.cli`, …) rather than in one `cli/commands/` package (feature colocation,
following BE-0257). A command with no owning feature stays a thin wrapper in
`cli/commands/` (`doctor` / `lint` / `schema` / `report`), still picked up by the directory
scan in `_build_app`. **Adding a feature command is adding it to `_FEATURE_MODULE_NAMES`**;
adding a feature-less one is dropping a `commands/<name>.py` file, no edit needed here.

`app` is built lazily, on first access (module `__getattr__`, PEP 562), not at import time.
Feature-colocation means most of those feature modules import back from here (`bajutsu.cli._shared`,
`.dotenv`, `.handoff`) — building `app` eagerly at import time would make importing *any* of them
(e.g. a test reaching `bajutsu.analysis.cli.audit` directly) recurse back into this module while it
is still mid-import, before the very module it needs has finished registering itself. Deferring the
build until something actually reads `bajutsu.cli.app` sidesteps that: by then every feature module
that triggered the reach-back has already finished its own top-level execution.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import cache
from typing import Any

import typer

from bajutsu.cli import commands
from bajutsu.cli.dotenv import load_dotenv

# Every feature's CLI module name, each exposing a `register(app)` — `serve.cli` and `analysis.cli`
# are themselves thin aggregators over several sibling files (BE-0257 follow-on: feature
# colocation keeps each original command in its own file, never merged). Sorted here for a
# stable --help order, mirroring the old directory scan's `sorted(...)`. Imported inside
# `_build_app`, not at module top — see the module docstring.
_FEATURE_MODULE_NAMES: tuple[str, ...] = (
    "bajutsu.analysis.cli",
    "bajutsu.codegen.cli",
    "bajutsu.crawl.cli",
    "bajutsu.mcp.cli",
    "bajutsu.record.cli",
    "bajutsu.run.cli",
    "bajutsu.serve.cli",
    "bajutsu.triage.cli",
)

# Rich help panels that split `bajutsu --help` on the Claude boundary (BE-0101), so the split is the
# first thing `--help` shows. Titles are the two buckets `capabilities` classifies into.
_CLAUDE_FREE_PANEL = "Claude-free (zero-config)"
_CLAUDE_USING_PANEL = "Uses Claude"


def _bootstrap(ctx: typer.Context) -> None:
    """Load a gitignored .env (e.g. ANTHROPIC_API_KEY) and wire up logging before any command runs."""
    from bajutsu import diagnostics

    load_dotenv()
    # After the .env, so a project can set BAJUTSU_LOG_LEVEL there alongside its other run settings.
    #
    # Every command but `serve`, which owns its logging and reads the same variable (BE-0055).
    # `oplog.configure` takes over the *root* logger with a secret-redacting sink seeded with the
    # operator token and the API key, and it can only clear root's handlers — one left here on the
    # `bajutsu` logger would survive it, write every record to stderr a second time, and write it
    # unredacted. A deployment that raises its serve log level must not thereby leak its secrets.
    if ctx.invoked_subcommand != "serve":
        diagnostics.configure()


def _register_commands(app: typer.Typer) -> None:
    """Register every feature's CLI module, then the feature-less `commands/<name>.py` ones.

    Both groups are visited in a fixed, sorted order for a stable --help listing.
    """
    for module_name in _FEATURE_MODULE_NAMES:
        importlib.import_module(module_name).register(app)
    for name in sorted(mod.name for mod in pkgutil.iter_modules(commands.__path__)):
        module = importlib.import_module(f"{commands.__name__}.{name}")
        module.register(app)


def _group_by_claude_use(app: typer.Typer) -> None:
    """Sort each command into the Claude-free / uses-Claude help panel from `capabilities` (BE-0101).

    Done once here rather than in each `commands/<name>.py` so the classification stays in one place
    and adding a command needs no help-panel wiring — its `capabilities` entry drives the panel.
    """
    from bajutsu import capabilities

    for info in app.registered_commands:
        name = info.name or (info.callback.__name__.replace("_", "-") if info.callback else "")
        cap = capabilities.by_command(name)
        if cap is not None:
            info.rich_help_panel = _CLAUDE_USING_PANEL if cap.uses_claude else _CLAUDE_FREE_PANEL


@cache
def _build_app() -> typer.Typer:
    """Assemble the Typer app once, memoized. See the module docstring for why this is lazy."""
    app = typer.Typer(add_completion=False, help="自然言語駆動の E2E テストツール")
    app.callback()(_bootstrap)
    _register_commands(app)
    _group_by_claude_use(app)
    return app


def __getattr__(name: str) -> Any:
    if name == "app":
        return _build_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["app"]


if __name__ == "__main__":
    _build_app()()
