"""The `bajutsu>` prompt loop: read one line, run it, print the answer, read the next (BE-0423)."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.repl.session import COMMAND_ERRORS, FATAL_ERRORS, ReplExit, ReplSession

PROMPT = "bajutsu> "


def repl_loop(
    session: ReplSession,
    read_line: Callable[[str], str],
    say: Callable[[str], None],
) -> None:
    """Drive the shell until the operator leaves — `exit` / `quit`, or the end of input.

    Args:
        session: The command set, bound to the launched app's driver.
        read_line: Prompts and returns one typed line (`input` on a terminal). `EOFError` ends the
            shell; `KeyboardInterrupt` abandons the half-typed line and keeps the shell open — but
            only while blocked here. An interrupt raised while a command is actually running (a slow
            `tap` against a wedged device) is not caught below and ends the shell, same as a bug.
        say: Writes one line of output (`typer.echo` on a terminal).
    """
    while True:
        try:
            line = read_line(PROMPT)
        except EOFError:
            say("")  # close the prompt's own line, so Ctrl-D doesn't leave the shell mid-line
            return
        except KeyboardInterrupt:
            say("")  # Ctrl-C abandons the half-typed line, as a shell does; `exit` leaves
            continue
        try:
            for out in session.dispatch(line):
                say(out)
        except ReplExit:
            return
        except FATAL_ERRORS as e:
            # Ordered before COMMAND_ERRORS, which would otherwise swallow this subclass: the
            # runner is gone and this shell has no respawn, so nothing typed next can succeed.
            say(f"{type(e).__name__}: {e}")
            say("the XCUITest runner is gone; this shell cannot recover it — leaving")
            return
        except COMMAND_ERRORS as e:
            # Named by its class, the way `run` reports the same failure, so an id that is wrong
            # here is wrong in a scenario for a reason the operator can already read.
            say(f"{type(e).__name__}: {e}")
