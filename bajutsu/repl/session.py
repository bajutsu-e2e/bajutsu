"""The `repl` command set (BE-0423): one method per verb, each a thin call on the shared `Driver`."""

from __future__ import annotations

import math
import re
import subprocess

from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import XcuitestChannelError, XcuitestRunnerCrashError
from bajutsu.common.drivers.xcuitest_live import WebDriverError
from bajutsu.common.run_meta.id import new_run_id
from bajutsu.repl.render import render_json, render_table

_INDEX_SUFFIX = re.compile(r"(.*)#(-?\d+)\Z")


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
    "tap <target>       tap the element <target> resolves to (see below)",
    "type <target> <text>   focus <target>, then type <text> (quote <target> if it has a space)",
    "back               navigate back one level",
    "screenshot [path]  write a screenshot; auto-named in the current directory when omitted",
    "help               this list",
    "exit | quit        leave the shell",
    "",
    "<target> is one of:",
    "  <id>              an element's id, verbatim (may contain spaces for `tap`, not `type`)",
    "  <id>#<index>       the nth of several elements sharing that id (0-based; negative from the end)",
    "  label:<text>      an element with no id, addressed by its exact label",
    "  label:<text>#<index>  the nth of several elements sharing that label",
    "  @<x>,<y>          a raw screen coordinate — `tap` only, bypasses the element tree entirely",
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
        usage = ["usage: tap <id>[#<index>] | tap label:<text>[#<index>] | tap @<x>,<y>"]
        if not rest:
            return usage
        target = _parse_target(rest)
        if target is None:
            return usage
        if isinstance(target, tuple):
            # Bypasses element resolution entirely — no `ElementNotFound`/`AmbiguousSelector` to
            # report, since there is no selector here for the driver to resolve.
            self._driver.tap_point(target)
            return [f"tapped {rest}"]
        # `driver.tap`, not the runner's `_tap_with_recovery`: while diagnosing a selector, the
        # driver's own `ElementNotTappable` — naming the element in the way — is the more useful
        # answer than a bounded scroll that quietly clears the obstruction. The deliberate v1 gap
        # is that `run` would recover where this reports a failure (BE-0423, *Alternatives considered*).
        self._driver.tap(target)
        return [f"tapped {rest}"]

    def _type(self, rest: str) -> list[str]:
        usage = [
            "usage: type <target> <text> — target is <id>[#<index>], label:<text>[#<index>], "
            'or "<quoted target>" when it carries a space'
        ]
        split = _split_target_and_text(rest)
        if split is None:
            return usage
        target_token, text = split
        target = _parse_target(target_token)
        if isinstance(target, tuple):
            return ["type does not support an @<x>,<y> coordinate target — tap it instead"]
        if target is None:
            return usage
        # Focus first: `type_text` acts on whatever the platform currently focuses, the same
        # contract the runner's own `type` step relies on.
        self._driver.tap(target)
        self._driver.type_text(text)
        return [f"typed into {target_token}"]

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


def _parse_target(token: str) -> base.Selector | base.Point | None:
    """A `tap`/`type` target token, as a `Selector` to resolve or a raw `Point` to tap directly.

    `None` marks a token this shell cannot parse at all (never a valid, just-unresolvable, selector
    — that distinction is `driver.tap`'s to raise as `ElementNotFound`/`AmbiguousSelector`, not this
    parser's). An id or label ending in a literal `#<digits>`, or starting with `@` or `label:`,
    cannot be reached this way — a narrow, documented gap (`help`), not a silent misread: `tree` /
    `find` remain the way to check what an element's id or label actually is.
    """
    if token.startswith("@"):
        return _parse_point(token[1:])
    if token.startswith("label:"):
        text, index = _split_index_suffix(token[len("label:") :])
        if not text:
            return None
        sel: base.Selector = {"label": text}
    else:
        text, index = _split_index_suffix(token)
        if not text:
            return None
        sel = {"id": text}
    if index is not None:
        sel["index"] = index
    return sel


def _split_index_suffix(text: str) -> tuple[str, int | None]:
    """*text*, with a trailing `#<int>` (0-based, negative-from-end) split off if present."""
    match = _INDEX_SUFFIX.match(text)
    if match is None:
        return text, None
    return match.group(1), int(match.group(2))


def _parse_point(text: str) -> base.Point | None:
    """`<x>,<y>` (the part of `@<x>,<y>` after the `@`) as a raw pixel `Point`, or `None`."""
    x, sep, y = text.partition(",")
    if not sep:
        return None
    try:
        point = (float(x), float(y))
    except ValueError:
        return None
    # `float` also reads `nan`/`inf`/`infinity`, none of which is a screen position: reject them
    # here rather than let one reach `tap_point` and fail opaquely inside a backend.
    return point if all(math.isfinite(c) for c in point) else None


def _split_target_and_text(rest: str) -> tuple[str, str] | None:
    """`type`'s remainder, split into its target token and the text to type.

    A target with an embedded space (a multi-word `label:...`) must be quoted — free-typed text
    never is, so quoting is never required for it and a stray `"` in it is never misread as a
    target delimiter. `None` marks a malformed line (no closing quote, or either half empty).
    """
    if rest.startswith('"'):
        closing = rest.find('"', 1)
        if closing == -1:
            return None
        target, text = rest[1:closing], rest[closing + 1 :].lstrip(" ")
    else:
        parts = rest.split(maxsplit=1)
        if len(parts) != 2:
            return None
        target, text = parts
    return (target, text) if target and text else None


def _table(elements: list[base.Element], *, empty: str) -> list[str]:
    """The rendered table, or the one-line reason there is none — never a bare header."""
    return render_table(elements).splitlines() if elements else [empty]


def _contains(el: base.Element, needle: str) -> bool:
    """Whether the element's id or label carries *needle* — the two fields the table leads with.

    Case-sensitive, like every selector match in the tool: a filter that quietly widened the match
    would answer a different question from the one the selector it is being used to check will ask.
    """
    return any(needle in (field or "") for field in (el["identifier"], el["label"]))
