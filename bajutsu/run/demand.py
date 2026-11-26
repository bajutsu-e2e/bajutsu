"""What a run would take from a lease broker: `python -m bajutsu.run.demand [run flags]` (BE-0511).

A worker borrows devices before it starts a run, so it has to know how many. This takes exactly the
flags of the run, loads the config and scenarios the way the run does (`run.cli.device_demand`), and
prints one JSON line. It resolves no device and decides no verdict.

A module run as a process of its own, in the run's directory and environment, so a relative path, a
Git config, and a secret resolve here as they will for the run.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

import typer

from bajutsu import cli as _cli
from bajutsu.run.cli import device_demand
from bajutsu.run.internal import PROG_NAME, build_app


def run_demand(args: Sequence[str]) -> dict[str, Any]:
    """The devices a run given *args* would borrow, as ``{"pools": [...], "workers": N}``.

    Raises:
        typer.Exit: The config or the scenarios do not load, as the run itself would report.
    """
    command = typer.main.get_command(build_app())
    return device_demand(command.make_context(PROG_NAME, list(args)).params)


def main() -> None:
    """Print the demand of the run the arguments describe as the last line of standard output."""
    _cli.bootstrap(configure_logging=True)
    try:
        demand = run_demand(sys.argv[1:])
    except typer.Exit as e:
        sys.exit(e.exit_code or 1)
    sys.stdout.write(json.dumps(demand) + "\n")


if __name__ == "__main__":
    main()
