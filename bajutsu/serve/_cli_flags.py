"""Derive serve's launch argv from the CLI's own option metadata (BE-0134).

serve spawns ``python -m bajutsu run/record/crawl …`` for its launch requests. Rather than
re-listing each flag's spelling and on/off form by hand — a second definition that drifts from the
CLI as flags are added or changed — render every flag from the single source of truth: the
``typer`` command's own ``click`` parameters. Adding a flag to the CLI command makes it renderable
here with no second edit, and a flag that was renamed/removed on the CLI is caught (``flag_args``
raises on a name it can't find).

The command's ``click`` object is built lazily and cached: importing a ``bajutsu.cli.commands``
module pulls in the whole CLI (which imports ``bajutsu.serve``), so building at import time would be
a cycle — every caller is well past both packages' import by the time it renders an argv.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from functools import cache
from typing import Literal

import typer
from typer.core import TyperCommand, TyperOption

# The commands serve spawns. Each is a `bajutsu.cli.commands.<name>` module whose command function
# shares the name — a Literal so a typo at a call site is a type error, not a runtime import failure.
Command = Literal["run", "record", "crawl"]
# A flag value: a string/int for a value option, a tri-state bool for a flag pair, or None to omit.
FlagValue = str | bool | int | None


@cache
def _command(name: Command) -> TyperCommand:
    """The ``click`` command typer builds for ``bajutsu <name>`` — the flag metadata source.

    Each ``bajutsu.cli.commands.<name>`` module exposes its command as a function of the same name.
    Imported here (not at module top) to keep the CLI ↔ serve import cycle from firing during
    package import; the result is cached, so the CLI is introspected once per command.
    """
    fn = getattr(importlib.import_module(f"bajutsu.cli.commands.{name}"), name)
    app = typer.Typer(add_completion=False)
    app.command()(fn)
    cmd = typer.main.get_command(app)
    # A single-command Typer app resolves to that command directly. A plain `assert` would be
    # stripped under `python -O`, taking the guard with it — so raise, to fail loudly if a future
    # typer change ever wraps it in a group (which would silently mis-read the flag surface).
    if not isinstance(cmd, TyperCommand):
        raise RuntimeError(f"expected a single command for `bajutsu {name}`, got {type(cmd)!r}")
    return cmd


def _options(name: Command) -> dict[str, TyperOption]:
    """``bajutsu <name>``'s options keyed by ``click`` parameter name — the classifiable surface."""
    cmd = _command(name)
    return {p.name: p for p in cmd.params if isinstance(p, TyperOption) and p.name}


def option_names(name: Command) -> frozenset[str]:
    """The ``click`` parameter names of ``bajutsu <name>``'s options — its full flag surface."""
    return frozenset(_options(name))


def flag_args(name: Command, values: Mapping[str, FlagValue]) -> list[str]:
    """Render argv fragments for ``bajutsu <name>``'s flags, keyed by ``click`` parameter name.

    Each value renders through the command's own option (its spelling and on/off form), so serve's
    argv can't drift from the CLI. A name that isn't an option on the command raises — the drift
    guard for a flag renamed or removed on the CLI side.

    Raises:
        ValueError: *values* names a parameter that isn't an option on the command.
    """
    opts = _options(name)
    args: list[str] = []
    for key, value in values.items():
        option = opts.get(key)
        if option is None:
            raise ValueError(f"{key!r} is not an option on `bajutsu {name}`")
        args += _render(option, value)
    return args


def _render(option: TyperOption, value: FlagValue) -> list[str]:
    """One flag's argv fragment; ``None`` / empty string emit nothing (leave the CLI default)."""
    if value is None or value == "":
        return []
    if option.is_bool_flag:
        # A bool-flag pair (--erase/--no-erase) emits either side; a store-true flag (--zip) has no
        # secondary, so a falsy value emits nothing — it can't be forced off.
        if value:
            return [option.opts[0]]
        return [option.secondary_opts[0]] if option.secondary_opts else []
    return [option.opts[0], str(value)]
