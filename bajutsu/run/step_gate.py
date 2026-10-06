"""The `bajutsu run --step` prompt: hold the step loop at a boundary and let an operator look."""

from __future__ import annotations

from collections.abc import Callable, Collection

from bajutsu.common.cancellation import RunCancelled
from bajutsu.common.orchestrator.types import StepPause
from bajutsu.repl.session import ACTUATING_VERBS, COMMAND_ERRORS, ReplSession

PROMPT = "step> "

_HELP = (
    "next | n | <enter>   run this step, then stop before the next one",
    "continue | c         run on until the next breakpoint (or the end)",
    "quit | q | exit      end the run here; it is reported as cancelled (at a failure, the failure",
    "                     itself is kept)",
    "Ctrl-C               ends the run, as it does without --step",
    "help                 this list, then the shell's own commands",
    "",
    "Any other line is a `repl` command run against the live app (tree, find, tap, type, ...).",
    "A command that acts on the app (tap, type, scroll, back, step) makes this run fail, whatever",
    "its assertions say: a hand-driven run is a debugging aid, never evidence the scenario passes.",
)


class PromptStepGate:
    """A `StepGate` that stops at chosen steps and reads commands from an operator.

    Stopping is deterministic bookkeeping over the run's own step indices and names; nothing here
    reads a model or touches an assertion, so the verdict stays the scenario's own.
    """

    def __init__(
        self,
        *,
        pause_first: bool,
        breaks: Collection[str],
        break_on_fail: bool,
        read_line: Callable[[str], str],
        say: Callable[[str], None],
    ) -> None:
        """Build a gate.

        Args:
            pause_first: Stop before every step until `continue`, starting with the first one.
            breaks: Step `name`s or step indices (decimal) to stop at.
            break_on_fail: Also stop on a failed step, while its screen is still up.
            read_line: Prompts and returns one typed line; `EOFError` ends the run.
            say: Writes one line of output.
        """
        self._stepping = pause_first
        self._breaks = set(breaks)
        self._hit: set[str] = set()
        self._break_on_fail = break_on_fail
        self._read_line = read_line
        self._say = say
        self._session: ReplSession | None = None
        self._session_driver: object | None = None
        self._manual_action: str | None = None

    @property
    def manual_action(self) -> str | None:
        """The first command that acted on the app during a pause, or `None` if none did."""
        return self._manual_action

    def before_step(self, pause: StepPause) -> bool:
        """Stop when stepping, or when this step's name or index is a breakpoint."""
        matched = self._matching_breaks(pause)
        if not (self._stepping or matched):
            return False
        self._hit |= matched
        name = f" [{pause.name}]" if pause.name else ""
        self._say(f"⏸ before step {pause.index}{name}: {pause.label}")
        self._prompt(pause, at_failure=False)
        return True

    def after_failure(self, pause: StepPause) -> None:
        """Stop on a failed step when stepping or `--break-on-fail`."""
        if not (self._stepping or self._break_on_fail):
            return
        self._say(f"✘ step {pause.index} failed: {pause.failure}")
        self._prompt(pause, at_failure=True)

    def unreached_breaks(self) -> list[str]:
        """The breakpoints no step matched — a typo in a name or an index past the last step."""
        return sorted(self._breaks - self._hit)

    def _matching_breaks(self, pause: StepPause) -> set[str]:
        keys = {str(pause.index)}
        if pause.name:
            keys.add(pause.name)
        return self._breaks & keys

    def _prompt(self, pause: StepPause, *, at_failure: bool) -> None:
        """Read commands until one lets the run go on; `quit` or end of input ends it.

        At a failure, ending the run is a plain return: the failure already ends it, and raising
        `RunCancelled` would report the scenario as cancelled instead of what actually failed.
        """
        while True:
            try:
                line = self._read_line(PROMPT).strip()
            except EOFError:
                self._say("")
                verb = "quit"
            except KeyboardInterrupt:
                # Not "abandon the half-typed line" as in `repl`: the terminal delivers Ctrl-C to the
                # run's whole process group, so the screen recorder, the device-log stream, and a
                # Playwright browser have already been interrupted. Ending the run says so honestly.
                self._say("")
                verb = "quit"
            else:
                verb = line.split(maxsplit=1)[0] if line else ""
            if verb in ("", "next", "n"):
                self._stepping = True
                return
            if verb in ("continue", "c"):
                self._stepping = False
                return
            if verb in ("quit", "q", "exit"):
                if at_failure:
                    self._stepping = False
                    return
                self._say("run ended at the prompt")
                raise RunCancelled
            if verb == "help":
                for out in _HELP:
                    self._say(out)
            self._run_command(pause, line, verb)

    def _run_command(self, pause: StepPause, line: str, verb: str) -> None:
        if verb in ACTUATING_VERBS and self._manual_action is None:
            # Recorded before the command runs: a tap that raised may still have moved the app.
            self._manual_action = line
        try:
            for out in self._shell(pause).dispatch(line):
                self._say(out)
        except COMMAND_ERRORS as exc:
            self._say(f"error: {exc}")

    def _shell(self, pause: StepPause) -> ReplSession:
        # One session per driver: `step`'s `select` + `copy` scope has to outlive a single command.
        if self._session is None or self._session_driver is not pause.driver:
            self._session = ReplSession(pause.driver)
            self._session_driver = pause.driver
        return self._session
