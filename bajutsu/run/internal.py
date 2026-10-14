"""The internal entry point of `bajutsu run`: `python -m bajutsu.run.internal [run flags]` (BE-0449).

The public `bajutsu run` is a thin wrapper that hands its flags to the one-shot worker path, and
`run_job` spawns whatever a job's `cmd` holds. Were that `cmd` the public command, the wrapper would
spawn itself. So the body of the command, `run_body`, runs here, and the serve spec builders and the
wrapper both spawn this module. It takes exactly the flags of `bajutsu run`, since it is the same
function behind them.

A module of its own rather than a hidden `bajutsu` subcommand: it never appears in `bajutsu --help`
or in the command classification of BE-0101, and the process that runs it imports the run body and
nothing else from the CLI, which keeps the one process the wrapper adds cheap to start.
"""

from __future__ import annotations

import os
import signal
import stat
import threading

import typer

from bajutsu import cli as _cli
from bajutsu.run import PARENT_FD_ENV
from bajutsu.run.cli import run_body

# What a usage error names the command as. The caller typed `bajutsu run`, so a refusal reads the same
# as it did before the run moved behind this module.
PROG_NAME = "bajutsu run"


def build_app() -> typer.Typer:
    """A single-command app around the run body, as tests invoke it in process."""
    app = typer.Typer(add_completion=False)
    app.command()(run_body)
    return app


def _watch_parent() -> None:
    """Cancel this run once the one-shot caller that spawned it is gone (BE-0449).

    The caller holds the write end of a pipe open while it waits (`PARENT_FD_ENV`). A caller killed
    by a signal it cannot catch never relays a cancel, and the run sits in a session of its own, so
    nothing else would stop it driving the device. End of file on the pipe sends this process the
    SIGTERM a cancel would, so the graceful shutdown (BE-0370) runs with its own deadline. Run by
    hand or by another caller, the variable is absent and nothing is watched.
    """
    raw = os.environ.pop(PARENT_FD_ENV, None)
    if raw is None:
        return
    try:
        fd = int(raw)
        # A stale number inherited from elsewhere may name some other open file: watch a pipe only.
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            return
        os.set_inheritable(fd, False)  # the run's own children have no business with it
    except (ValueError, OSError):
        return

    def _wait() -> None:
        try:
            while os.read(fd, 1):
                pass
        except OSError:
            return  # an unreadable pipe says nothing about the caller
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_wait, name="parent-watch", daemon=True).start()


def main() -> None:
    """Run the `bajutsu` app's shared setup (`bajutsu.cli.bootstrap`), then the body."""
    _watch_parent()
    _cli.bootstrap(configure_logging=True)
    build_app()(prog_name=PROG_NAME)


if __name__ == "__main__":
    main()
