"""Invoke `bajutsu run` in process, through its internal entry point (BE-0449).

The public `bajutsu run` spawns the run body as a child process, so a `CliRunner` invocation of it
would see neither the test's monkeypatches nor the child's output. The CLI tests exercise the body's
own logic, so they invoke the internal entry point's app in process instead; the subprocess parity
tests in `test_run_wrapper.py` cover the wrapper.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import typer
from typer.testing import CliRunner, Result

from bajutsu.run.internal import build_app

RUN_APP = build_app()


def invoke(runner: CliRunner, app: typer.Typer, args: Sequence[str], **kwargs: Any) -> Result:
    """`runner.invoke(app, args)`, routing a `run` invocation to the internal entry point."""
    if args and args[0] == "run":
        return runner.invoke(RUN_APP, list(args[1:]), **kwargs)
    return runner.invoke(app, list(args), **kwargs)
