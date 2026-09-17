"""The `repl` command set (BE-0423): one method per verb, each a thin call on the shared `Driver`."""

from __future__ import annotations

import subprocess

from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import XcuitestChannelError, XcuitestRunnerCrashError
from bajutsu.common.drivers.xcuitest_live import WebDriverError
from bajutsu.common.run_meta.id import new_run_id
from bajutsu.repl.render import render_json, render_table


class ReplExit(Exception):
    """`exit` / `quit` was typed. The shell's own control flow, not a failure."""


# What a typed command can legitimately end in: a selector matching nothing or several elements,
# an element another one covers, a verb this backend cannot perform, a device or browser refusal,
# and a screenshot path the filesystem rejects. Each is an answer to the operator's question, so
# the loop prints it and reads the next line. Anything else is a bug and propagates.
#
# `subprocess.CalledProcessError`, `XcuitestChannelError`, and `WebDriverError` are here for the
# backends that don't wrap every device-side failure into `DeviceError` before it reaches a `Driver`
# method: adb's action methods bottom out in a bare `subprocess.run(..., check=True)`, the local
# XCUITest runner channel raises its own `RuntimeError` subclass on a lost/bad response (a failed
# tap, type, or screenshot request), and the `--udid https://…` live route's WebDriver calls raise
# a sibling `RuntimeError` subclass. Without all three, a USB flake, a wedged runner, or a grid
# hiccup would kill the whole interactive session instead of reading like any other refusal.
#
# `XcuitestRunnerCrashError` — the runner died and stayed unreachable past the driver's own
# transient-retry budget — is deliberately *not* here even though it subclasses
# `XcuitestChannelError`: everywhere else in the tool it is also a `base.BackendCrashError`, whose
# answer is to discard the lease and cold-respawn (`runner/pipeline.py`'s `except BackendCrashError`).
# `repl` has no respawn, and `_close_owned_session` deliberately leaves the local XCUITest
# environment running, so treating it as an ordinary command error would print one line and prompt
# again against a permanently dead driver — every later command failing the same way with nothing
# telling the operator the session can no longer answer anything. `FATAL_ERRORS` below ends the shell
# instead.
COMMAND_ERRORS: tuple[type[Exception], ...] = (
    base.SelectorError,
    base.ElementNotTappable,
    base.UnsupportedAction,
    device_errors.DeviceError,
    subprocess.CalledProcessError,
    XcuitestChannelError,
    WebDriverError,
    OSError,
)

# A dead runner is not an answer to the operator's question; it ends the shell rather than being
# reported and prompting again against a driver that can no longer answer anything.
FATAL_ERRORS: tuple[type[Exception], ...] = (XcuitestRunnerCrashError,)

_HELP = (
    "tree [--json]      the current element tree, as a table (or verbatim as JSON)",
    "find <substring>   the same tree, filtered to ids and labels containing <substring>",
    "tap <id>           tap the element with that id",
    "type <id> <text>   focus that element, then type <text>",
    "back               navigate back one level",
    "screenshot [path]  write a screenshot; auto-named in the current directory when omitted",
    "help               this list",
    "exit | quit        leave the shell",
)


class ReplSession:
    """One shell against one launched app: run a typed line against the driver, render the answer.

    Holds nothing but the driver. Every command reads the live screen, so two `tree`s never
    disagree because of something the shell cached between them.
    """

    def __init__(self, driver: base.Driver) -> None:
        self._driver = driver

    def dispatch(self, line: str) -> list[str]:
        """Run one typed line and return the lines to print (empty when there is nothing to say).

        Raises:
            ReplExit: The line was `exit` or `quit`.
        """
        parts = line.split(maxsplit=1)
        verb = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        match verb:
            case "":
                return []
            case "tree":
                return self._tree(rest)
            case "find":
                return self._find(rest)
            case "tap":
                return self._tap(rest)
            case "type":
                return self._type(rest)
            case "back":
                return self._back(rest)
            case "screenshot":
                return self._screenshot(rest)
            case "help":
                return list(_HELP)
            case "exit" | "quit":
                raise ReplExit
            case _:
                return [f"unknown command: {verb} (type `help` for the command set)"]

    def _tree(self, rest: str) -> list[str]:
        if rest not in ("", "--json"):
            return ["usage: tree [--json]"]
        elements = self._read()
        if rest == "--json":
            return render_json(elements).splitlines()
        return _table(elements, empty="the screen reports no elements")

    def _find(self, rest: str) -> list[str]:
        if not rest:
            return ["usage: find <substring>"]
        matched = [el for el in self._read() if _contains(el, rest)]
        return _table(matched, empty=f"no id or label contains {rest!r}")

    def _tap(self, rest: str) -> list[str]:
        if not rest:
            return ["usage: tap <id>"]
        # `driver.tap`, not the runner's `_tap_with_recovery`: while diagnosing a selector, the
        # driver's own `ElementNotTappable` — naming the element in the way — is the more useful
        # answer than a bounded scroll that quietly clears the obstruction. The deliberate v1 gap
        # is that `run` would recover where this reports a failure (BE-0423, *Alternatives considered*).
        self._driver.tap({"id": rest})
        return [f"tapped {rest}"]

    def _type(self, rest: str) -> list[str]:
        parts = rest.split(maxsplit=1)
        if len(parts) != 2:
            return ["usage: type <id> <text>"]
        target, text = parts
        # Focus first: `type_text` acts on whatever the platform currently focuses, the same
        # contract the runner's own `type` step relies on.
        self._driver.tap({"id": target})
        self._driver.type_text(text)
        return [f"typed into {target}"]

    def _back(self, rest: str) -> list[str]:
        if rest:
            return ["usage: back"]
        self._driver.back()
        return ["went back"]

    def _screenshot(self, rest: str) -> list[str]:
        # Auto-named from the shared run-id timestamp shape, so a shell's screenshots sort
        # chronologically beside a run's the same way.
        path = rest or f"{new_run_id('repl-')}.png"
        self._driver.screenshot(path)
        return [f"wrote {path}"]

    def _read(self) -> list[base.Element]:
        """The tree to print or filter, asked for the same way `run`'s own handlers ask for one.

        A backend whose reads must settle first (adb) answers through `settled_query`, so a `tree`
        typed right after a `tap` cannot describe the pre-tap screen (the read-lag barrier, BE-0332).
        """
        driver = self._driver
        if isinstance(driver, base.SettledReadProvider):
            return driver.settled_query()
        return driver.query()


def _table(elements: list[base.Element], *, empty: str) -> list[str]:
    """The rendered table, or the one-line reason there is none — never a bare header."""
    return render_table(elements).splitlines() if elements else [empty]


def _contains(el: base.Element, needle: str) -> bool:
    """Whether the element's id or label carries *needle* — the two fields the table leads with.

    Case-sensitive, like every selector match in the tool: a filter that quietly widened the match
    would answer a different question from the one the selector it is being used to check will ask.
    """
    return any(needle in (field or "") for field in (el["identifier"], el["label"]))
