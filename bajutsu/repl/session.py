"""The `repl` command set (BE-0423): one method per verb, each a thin call on the shared `Driver`."""

from __future__ import annotations

import math
import re
import subprocess

import yaml
from pydantic import ValidationError

from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base
from bajutsu.common.drivers.xcuitest import XcuitestChannelError, XcuitestRunnerCrashError
from bajutsu.common.drivers.xcuitest_live import WebDriverError
from bajutsu.common.orchestrator.actions import _action_of, _do_action
from bajutsu.common.orchestrator.types import SelectionState
from bajutsu.common.run_meta.id import new_run_id
from bajutsu.common.scenario import Selector as SelectorModel
from bajutsu.common.scenario import Step
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
    "scroll @<x1>,<y1> @<x2>,<y2>   drag/scroll from one raw coordinate to another",
    "back               navigate back one level",
    "screenshot [path]  write a screenshot; auto-named in the current directory when omitted",
    "step <yaml>        run one scenario step verbatim (see below)",
    "help               this list",
    "exit | quit        leave the shell",
    "",
    "<target> is one of:",
    "  <id>              an element's id, verbatim (may contain spaces for `tap`, not `type`)",
    "  <id>#<index>       the nth of several elements sharing that id (0-based; negative from the end)",
    "  label:<text>      an element with no id, addressed by its exact label",
    "  label:<text>#<index>  the nth of several elements sharing that label",
    "  @<x>,<y>          a raw screen coordinate — `tap` only, bypasses the element tree entirely",
    "  --sel <yaml>      a full scenario selector, e.g. --sel {idMatches: row.*, index: 1} — the",
    "                    same id/idMatches/label/labelMatches/traits/value/within/index vocabulary",
    "                    `run` accepts; must be one flow-style {...} mapping (no block YAML)",
    "",
    "step's <yaml> is exactly a scenario step — one flow-style {...} mapping, e.g.:",
    "  step {swipe: {on: {id: card}, direction: up}}",
    "  step {pinch: {sel: {id: map}, scale: 0.5}}",
    "any one-shot action a scenario's own steps accept works this way — tap, type, scroll, swipe,",
    "drag, pinch, rotate, setPickerValue, selectOption, select, copy, handleSystemAlert, and more —",
    "except wait / assert / control-flow (if/forEach/web/app) / use, which need a whole scenario to run",
)


class ReplSession:
    """One shell against one launched app: run a typed line against the driver, render the answer.

    Holds the driver plus the two bits of state `step` needs across separate calls: every other
    command reads the live screen fresh, so two `tree`s never disagree because of something the
    shell cached between them, but `select` + `copy` (BE-0265) and `generate` are meaningless
    without a scope that outlives the one `step` call that set them.
    """

    def __init__(self, driver: base.Driver) -> None:
        self._driver = driver
        self._selection = SelectionState()
        self._bindings: dict[str, str] = {}

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
            case "scroll":
                return self._scroll(rest)
            case "back":
                return self._back(rest)
            case "screenshot":
                return self._screenshot(rest)
            case "step":
                return self._step(rest)
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
        usage = [
            "usage: tap <id>[#<index>] | tap label:<text>[#<index>] | tap @<x>,<y> | "
            "tap --sel <yaml>"
        ]
        if not rest:
            return usage
        sel_arg = _strip_sel_flag(rest)
        if sel_arg is not None:
            split = _split_yaml_selector(sel_arg)
            if split is None or split[1]:  # no {...}, or trailing text `tap` has no use for
                return usage
            try:
                sel = _parse_yaml_selector(split[0])
            except _InvalidYamlSelector as e:
                return str(e).splitlines()
            self._driver.tap(sel)
            return [f"tapped {rest}"]
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
            'or "<quoted target>" when it carries a space; or type --sel <yaml> <text>'
        ]
        sel_arg = _strip_sel_flag(rest)
        if sel_arg is not None:
            split = _split_yaml_selector(sel_arg)
            if split is None or not split[1]:  # no {...}, or no text left to type
                return usage
            yaml_text, text = split
            try:
                sel = _parse_yaml_selector(yaml_text)
            except _InvalidYamlSelector as e:
                return str(e).splitlines()
            self._driver.tap(sel)
            self._driver.type_text(text)
            return [f"typed into {yaml_text}"]
        split2 = _split_target_and_text(rest)
        if split2 is None:
            return usage
        target_token, text = split2
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

    def _scroll(self, rest: str) -> list[str]:
        usage = ["usage: scroll @<x1>,<y1> @<x2>,<y2>"]
        parts = rest.split()
        if len(parts) != 2:
            return usage
        frm = _parse_at_point(parts[0])
        to = _parse_at_point(parts[1])
        if frm is None or to is None:
            return usage
        self._driver.scroll(frm, to)
        return [f"scrolled {rest}"]

    def _step(self, rest: str) -> list[str]:
        usage = ["usage: step <yaml> — one scenario step, e.g. step {tap: {id: row.1}}"]
        if not rest:
            return usage
        try:
            step = _parse_yaml_step(rest)
        except _InvalidYamlStep as e:
            return str(e).splitlines()
        try:
            kind = _action_of(step)
        except AssertionError:
            # `use` is the one `Step` field with no runtime action kind at all — a compile-time
            # macro the run expands away before dispatch — so `_action_of` itself has nothing to
            # name; report the same way an unhandled kind below does, rather than let its own
            # bare `AssertionError` escape.
            return ["step kind 'use' needs `run`/a scenario — it expands away before dispatch"]
        try:
            _do_action(self._driver, step, bindings=self._bindings, selection=self._selection)
        except AssertionError as e:
            if str(e) != "unhandled action":
                raise  # a handler's own internal assert — a real bug, not a missing run loop
            # `_do_action` covers every one-shot action kind (BE-0423's whole point is skipping
            # `run`/a scenario for exactly those); `wait`/`assert`/control-flow steps have no
            # handler there at all — they need a run loop, not this shell.
            return [
                f"step kind {_yaml_key(kind)!r} needs `run`/a scenario — the shell has no run loop"
            ]
        if kind == "generate":
            # `bindings` (above) gives it a real scope to write into instead of silently no-op'ing
            # on the `is None` early-return `_do_generate` takes for a bare condition eval — show
            # the value so the write is visible rather than merely not-silent.
            assert step.generate is not None
            key = f"vars.{step.generate.into.var}"
            return [f"ran step: generate ({key} = {self._bindings[key]!r})"]
        return [f"ran step: {_yaml_key(kind)}"]

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


def _parse_at_point(token: str) -> base.Point | None:
    """`@<x>,<y>` as a raw pixel `Point` — `None` when *token* isn't that shape at all."""
    if not token.startswith("@"):
        return None
    return _parse_point(token[1:])


class _InvalidYamlSelector(Exception):
    """A `--sel <yaml>` argument that didn't parse as YAML or didn't validate as a `Selector`."""


def _strip_sel_flag(rest: str) -> str | None:
    """The text after a leading `--sel` token, or `None` when *rest* doesn't start with one."""
    if rest == "--sel":
        return ""
    if rest.startswith("--sel "):
        return rest[len("--sel ") :].lstrip()
    return None


def _split_yaml_selector(rest: str) -> tuple[str, str] | None:
    """*rest* split into a flow-style `{...}` YAML mapping and whatever text follows it.

    Flow style only — the only YAML shape one typed line can hold unambiguously without block
    style's newlines — so the closing `}` is an unambiguous boundary, the same role a quote plays
    for a spaced `label:` target. `None` marks a line that doesn't open with `{` or never closes it.
    """
    rest = rest.lstrip()
    if not rest.startswith("{"):
        return None
    depth = 0
    for i, ch in enumerate(rest):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return rest[: i + 1], rest[i + 1 :].lstrip()
    return None  # unbalanced braces


def _parse_yaml_selector(text: str) -> base.Selector:
    """A `--sel` argument's flow-style YAML mapping, as a resolvable `Selector`.

    Reuses the same `Selector` pydantic model (and its `id`/`idMatches`/`label`/`labelMatches`/
    `traits`/`value`/`within`/`index` vocabulary, camelCase aliases included) a scenario step
    authors against, so this is the one target form with no vocabulary gap — everything `run`
    accepts, `tap`/`type` can now express too.

    Raises:
        _InvalidYamlSelector: *text* isn't valid YAML, isn't a mapping, or fails `Selector`'s own
            validation (an unknown field, a malformed `id` candidate list, no condition at all).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise _InvalidYamlSelector(f"invalid --sel YAML: {e}") from e
    try:
        # `model_validate` itself rejects a non-mapping (e.g. the flow-set shorthand `{a, b}`,
        # valid YAML but not a `Selector`) with its own clear message — no separate dict check
        # needed on top of it.
        return SelectorModel.model_validate(data).as_selector()
    except ValidationError as e:
        raise _InvalidYamlSelector(f"invalid --sel selector: {e}") from e


class _InvalidYamlStep(Exception):
    """A `step <yaml>` argument that didn't parse as YAML or didn't validate as a `Step`."""


def _parse_yaml_step(text: str) -> Step:
    """A `step` argument's YAML, as the exact `Step` model a scenario's own steps validate against.

    Unlike `--sel`, *text* is the whole remainder of the line — nothing follows a step, so there is
    no brace-balance split to do first, only the YAML load and the model's own validation.

    Raises:
        _InvalidYamlStep: *text* isn't valid YAML, isn't a mapping, or fails `Step`'s own
            validation (an unknown field, more than one action set, or no action at all).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise _InvalidYamlStep(f"invalid step YAML: {e}") from e
    try:
        return Step.model_validate(data)
    except ValidationError as e:
        raise _InvalidYamlStep(f"invalid step: {e}") from e


def _yaml_key(kind: str) -> str:
    """An action's own YAML key — `_action_of` returns the Python field name, which differs from
    it exactly where `Step` aliases one (`copy_` -> `copy`, `if_` -> `if`, `for_each` -> `forEach`,
    ...); reporting the alias is what an operator can actually paste back into another `step` line.
    """
    return Step.model_fields[kind].alias or kind


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
