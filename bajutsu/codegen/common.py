"""Shared scenario walk for the codegen emitters (BE-0083).

XCUITest (`xcuitest.py`) and Playwright (`playwright.py`) transpile a scenario the same way
— merge the launch environment, open the test, emit a launch line, emit each step, then the
`expect` block, and close — differing only in the per-line target syntax. That walk lives here
once; each target supplies the variable parts through the `CodeGenerator` protocol, so adding a
third target (e.g. an Android emitter) is the cost of its line syntax alone, not another copy of
the skeleton.

This is a pure, deterministic transform: no AI, no device. The per-line builders (`step_lines` /
`assertion_lines` and the selector/locator helpers behind them) stay in each target's module.
"""

from __future__ import annotations

import re
from typing import Protocol

from bajutsu.scenario import Assertion, Scenario, Step
from bajutsu.scenario.models.actions import bypass_hint


class CodegenError(ValueError):
    """A codegen request that cannot be fulfilled.

    Raised at generation time (never a silent stub): an unknown emit, an emit on the wrong target
    (Playwright needs a web target, UI Automator an Android target), or a scenario construct no
    target can translate faithfully (`if` / `forEach` control flow or an `extract` capture, BE-0297).
    Both transports — the `codegen` CLI and the serve `/api/codegen` endpoint — translate it into
    their own error surface.
    """


# Body lines (launch env, launch, steps, the expect block) sit one level inside the test function;
# the structural braces (`scenario_open` / `scenario_close`) carry their own indent. Both targets
# comment in C-style, so the `// expect` divider is shared.
_BODY_INDENT = "    "
_EXPECT_COMMENT = "// expect"

# Regex metacharacters. A `labelMatches` value is a Python `re.search` pattern; only a
# metacharacter-free one is a plain substring a black-box target can map faithfully (NSPredicate
# `CONTAINS` on XCUITest, `By.textContains` on UI Automator). A real regex has no faithful form on
# either — NSPredicate `MATCHES` and `By.text(Pattern)` are full, differently-anchored matches — so
# it stays a `// TODO`. Shared here so a third target inherits the same substring/regex split.
_RE_METACHARS = set(r".^$*+?{}[]\|()")

# Every character that ends a `//` line comment in the generated targets — a lone `\r`, `\n`, a
# `\r\n`, and the Unicode line/paragraph separators (U+2028 / U+2029) — so agent-authored free text
# folded into a `// TODO` reason can never spill onto an unprefixed physical line (BE-0185).
_LINE_TERMINATORS = re.compile(r"[\r\n\u2028\u2029]+")


def _collapse_line_terminators(text: str) -> str:
    """Fold any run of line terminators into a single space so text stays on one `//` comment line."""
    return _LINE_TERMINATORS.sub(" ", text)


def ident(name: str) -> str:
    """Turn a scenario name into a test-method identifier (`test_`-prefixed, digit-safe).

    Swift and Kotlin both forbid a bare identifier starting with a digit and allow only
    `[0-9a-zA-Z_]`, so the sanitization is language-agnostic; a JS/TS target emitting a `test(...)`
    call with a string label does not need it.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_")
    if not cleaned:
        cleaned = "scenario"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return f"test_{cleaned}"


def class_name(name: str, suffix: str) -> str:
    """Turn a file stem into a PascalCase test-class name with `suffix` appended.

    Args:
        name: The file stem to derive the class name from.
        suffix: The per-target class-name suffix (`"UITests"` for XCUITest, `"UITest"` for UI
            Automator).

    The digit-prefix guard applies to every target: a Swift or Kotlin `class` name has the same
    no-leading-digit restriction as a method identifier, so a digit-leading stem is prefixed `_`.
    """
    cleaned = re.sub(r"[^0-9a-zA-Z]+", " ", name).title().replace(" ", "")
    if not cleaned:
        cleaned = "Generated"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return f"{cleaned}{suffix}"


def ms(seconds: float) -> int:
    """Convert a `float` seconds duration into an `int` milliseconds count for a generated call."""
    return int(seconds * 1000)


def is_plain_substring(pattern: str) -> bool:
    """Whether a `labelMatches` pattern is a metacharacter-free plain substring (not a real regex).

    True means the pattern can map faithfully to a native substring-contains call; False means it is
    a real regex with no faithful native form (the caller emits a `// TODO`).
    """
    return not (set(pattern) & _RE_METACHARS)


def network_unsupported(subject: str) -> str:
    """The `// TODO` reason for a network assertion on a target with no interception surface.

    Args:
        subject: The backend named in the message (`"XCUITest"`, `"the adb backend"`).

    Both black-box mobile targets emit the same shaped `// TODO` for a `request` assertion or
    `until: { request }` wait; only the named backend differs.
    """
    return f"{subject} has no network interception; assert via a mock/proxy; not generated"


def manual_todo(label: str, bypass: str | None) -> str:
    """The `// TODO` reason for a `manual` human-takeover step (BE-0185), shared by every target.

    Args:
        label: What the human did (the recorded operation, e.g. "solve the CAPTCHA").
        bypass: A deterministic bridge to wire (a test-build flag, a device-control / device-state
            primitive) when one exists, or None for an operation with no run-time equivalent.

    An operation only a human can perform has no generated-test form on any backend, so — like the
    `setLocation` / `push` device-control TODOs — it renders as a labeled `// TODO` naming what to
    wire (`bypass`) or that nothing can (a real CAPTCHA), never a silent skip that would fake a pass.
    """
    # `label`/`bypass` are agent-authored free text (only secret-masked, never newline-stripped), so
    # any line terminator embedded in one would break out of the `// TODO` comment into a new,
    # unprefixed physical line of generated source that CI then compiles. JavaScript ends a `//`
    # comment at U+2028 / U+2029 too (ECMA-262 `LineTerminator`), while Swift and Kotlin/Java end one
    # only at LF/CR/CRLF; collapsing all of them in this one shared helper is harmless where a target
    # is stricter and keeps the whole reason on the comment line, mirroring `uiautomator.py`'s `_s`.
    safe_label = _collapse_line_terminators(label)
    safe_bypass = _collapse_line_terminators(bypass) if bypass else None
    return f"{safe_label} — {bypass_hint(safe_bypass)}; not generated"


def permissions_setup_lines(scenario: Scenario) -> list[str]:
    """The `// TODO` lines naming each `permissions` entry (BE-0276), one per service.

    No target here generates app-level test code that can pre-set OS permission state (bajutsu
    applies the field itself, before the generated test's launch step runs) — the same
    "labeled TODO, not generated" shape as the `setLocation` / `push` step TODOs (BE-0026), and
    named per service (not the field as a whole) so a scenario with a mixed grant/revoke set is
    unambiguous in the generated output. Shared by every target via `setup_lines` since none can
    represent it.
    """
    return [
        f"// TODO: permissions.{service} ({action}) — bajutsu applies this before launch; not generated"
        for service, action in scenario.permissions.items()
    ]


class CodeGenerator(Protocol):
    """The target-specific parts of a generated test file.

    The shared walk supplies the structure (scenario loop, env merge, body indentation, the
    expect divider); a generator supplies only the syntax of each line for its target language.
    """

    def file_preamble(self) -> list[str]:
        """The lines before the first scenario (header comment, imports, class/describe open)."""

    def scenario_open(self, name: str) -> str:
        """The line opening one scenario's test function/case (carries its own indent)."""

    def setup_lines(self, scenario: Scenario) -> list[str]:
        """Per-scenario setup emitted before the launch (un-indented); empty when none is needed.

        The hook a target uses to install observers that must be in place before navigation — e.g.
        the Playwright network-exchange recorder, so a request assertion can read traffic that
        happened during the steps, not only future traffic.
        """

    def launch_env_line(self, key: str, value: str) -> str:
        """One launch-environment assignment (un-indented; the walk adds the body indent)."""

    def launch_line(self) -> str:
        """The line that launches/navigates the app (un-indented)."""

    def step_lines(self, step: Step) -> list[str]:
        """The lines for one scenario step (un-indented)."""

    def assertion_lines(self, assertion: Assertion) -> list[str]:
        """The lines for one `expect` assertion (un-indented)."""

    def scenario_close(self) -> str:
        """The line closing one scenario's test function/case (carries its own indent)."""

    def file_footer(self) -> list[str]:
        """The lines after the last scenario (class/describe close)."""


# `if` / `forEach` / `extract` are evaluated at run time against the live UI tree — a branch on the
# current state, a loop over the live match set, a capture of a resolved element's property. A static
# generated test has no runtime to reproduce that, so no target emits them (they fell through to a
# no-op `// TODO` stub before BE-0297). Silently dropping a whole branch or loop body is exactly the
# degradation the determinism-first directive forbids, so codegen refuses loudly at generation time
# and names `bajutsu run` as the faithful path — rather than emitting a test that quietly does less.
_RUNTIME_ONLY_HINT = "codegen has no runtime to evaluate it; run the scenario with `bajutsu run`"


def _reject_runtime_only(step: Step) -> None:
    """Fail loudly on a runtime-only construct no target can translate to a static test (BE-0297)."""
    if step.if_ is not None:
        raise CodegenError(
            f"codegen does not support the `if` control-flow step — {_RUNTIME_ONLY_HINT}"
        )
    if step.for_each is not None:
        raise CodegenError(
            f"codegen does not support the `forEach` control-flow step — {_RUNTIME_ONLY_HINT}"
        )
    if step.extract is not None:
        raise CodegenError(f"codegen does not support the `extract` capture — {_RUNTIME_ONLY_HINT}")


def _scenario_lines(
    scenario: Scenario, app_launch_env: dict[str, str], gen: CodeGenerator
) -> list[str]:
    env = {**app_launch_env, **scenario.preconditions.launch_env}
    body: list[str] = list(gen.setup_lines(scenario))
    body.extend(gen.launch_env_line(k, v) for k, v in env.items())
    body.append(gen.launch_line())
    body.append("")
    for step in scenario.steps:
        _reject_runtime_only(step)
        body.extend(gen.step_lines(step))
    if scenario.expect:
        body.append("")
        body.append(_EXPECT_COMMENT)
        for assertion in scenario.expect:
            body.extend(gen.assertion_lines(assertion))
    return [
        gen.scenario_open(scenario.name),
        *(f"{_BODY_INDENT}{line}" if line else "" for line in body),
        gen.scenario_close(),
    ]


def render_test_file(
    scenarios: list[Scenario], app_launch_env: dict[str, str] | None, gen: CodeGenerator
) -> str:
    """Render the whole test file: preamble, one block per scenario (blank-line separated), footer."""
    env = app_launch_env or {}
    body: list[str] = list(gen.file_preamble())
    for scenario in scenarios:
        body.extend(_scenario_lines(scenario, env, gen))
        body.append("")
    body.extend(gen.file_footer())
    return "\n".join(body) + "\n"
