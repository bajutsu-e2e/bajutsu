"""The target-independent half of codegen: naming, escaping, and assembling a generated file."""

from __future__ import annotations

import re

from bajutsu.common.scenario import Scenario, Step
from bajutsu.common.scenario.models.actions import bypass_hint

from .code_generator import CodeGenerator
from .codegen_error import CodegenError

# Body lines (launch env, launch, steps, the expect block) sit one level inside the test function;
# the structural braces (`scenario_open` / `scenario_close`) carry their own indent. Both targets
# comment in C-style, so the `// expect` divider is shared.
_BODY_INDENT = "    "
_EXPECT_COMMENT = "// expect"
# The `before` phase (BE-0392) needs no per-target construct: emitting its steps inline at the top of
# the test body is exactly its runtime meaning — they run first, and a failure aborts the rest. The
# divider is what keeps them readable as a phase rather than as more of the scenario's own steps.
_BEFORE_COMMENT = "// before (bajutsu lifecycle phase)"

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


# `if` / `forEach` / `extract` are evaluated at run time against the live UI tree — a branch on the
# current state, a loop over the live match set, a capture of a resolved element's property. A static
# generated test has no runtime to reproduce that, so no target emits them (they fell through to a
# no-op `// TODO` stub before BE-0297). Silently dropping a whole branch or loop body is exactly the
# degradation the determinism-first directive forbids, so codegen refuses loudly at generation time
# and names `bajutsu run` as the faithful path — rather than emitting a test that quietly does less.
_RUNTIME_ONLY_HINT = "codegen has no runtime to evaluate it; run the scenario with `bajutsu run`"


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


def device_group_todo(step: Step) -> str | None:
    """The `// TODO` for an `installApp` or `setPrimaryTarget` step (BE-0447), else None.

    `installApp` installs another device-group member's build on a shared device; `setPrimaryTarget`
    only moves the target later steps follow, and is legal with a single declared target, the one
    shape that reaches these generators today. A generated test drives one app on one device, so
    neither has a form there; like the device-control TODOs, each renders as a labeled comment,
    never a silent skip.
    """
    if step.install_app is not None:
        return (
            f"// TODO: installApp(from: {_collapse_line_terminators(step.install_app.from_)}) — "
            "installs another target's build on a shared device; not generated"
        )
    if step.set_primary_target is not None:
        return (
            "// TODO: setPrimaryTarget(target: "
            f"{_collapse_line_terminators(step.set_primary_target.target)}) — "
            "moves the default target across targets; not generated"
        )
    return None


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


def interrupts_setup_lines(scenario: Scenario) -> list[str]:
    """The `// TODO` lines naming each `interrupts` handler (BE-0314), one per entry.

    No native XCUITest / Espresso / Playwright construct maps onto "check this condition
    opportunistically throughout the whole test," so — like `permissions_setup_lines` — this renders
    a labeled `// TODO` naming the field and each entry's condition rather than a silent skip. Only a
    scenario's own `interrupts` are visible to codegen (the walk sees the scenario, not the resolved
    config), matching how `permissions_setup_lines` emits only `scenario.permissions`.
    """
    return [
        f"// TODO: interrupts[{i}] "
        f"{_collapse_line_terminators(str(entry.condition.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)))}"
        " — bajutsu checks this opportunistically at run time; not generated"
        for i, entry in enumerate(scenario.interrupts)
    ]


def indent_lines(lines: list[str], levels: int = 1) -> list[str]:
    """Indent `lines` by `levels` body levels, leaving blank lines blank.

    The teardown emitters build nested blocks (a `finally`, an outcome `if`) whose contents must sit
    one level in from the wrapped body the walk already indents — same unit, so the two agree.
    """
    pad = _BODY_INDENT * levels
    return [f"{pad}{line}" if line else "" for line in lines]


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
    for rule in scenario.after:
        for hook_step in rule.steps:
            _reject_runtime_only(hook_step)
    after = gen.after_lines(scenario.after)
    body: list[str] = list(gen.setup_lines(scenario))
    body.extend(gen.launch_env_line(k, v) for k, v in env.items())
    body.append(gen.launch_line())
    body.append("")
    # Everything the teardown must observe sits inside the wrap — `before` included, since a failing
    # `before` step dispatches `after` as an `error` outcome at run time too.
    body.extend(after.prologue)
    inner: list[str] = []
    if scenario.before:
        inner.append(_BEFORE_COMMENT)
        for step in scenario.before:
            _reject_runtime_only(step)
            inner.extend(gen.step_lines(step))
        inner.append("")
    for step in scenario.steps:
        _reject_runtime_only(step)
        inner.extend(gen.step_lines(step))
    if scenario.expect:
        inner.append("")
        inner.append(_EXPECT_COMMENT)
        for assertion in scenario.expect:
            inner.extend(gen.assertion_lines(assertion))
    pad = _BODY_INDENT * after.body_indent
    body.extend(f"{pad}{line}" if line else "" for line in inner)
    body.extend(after.epilogue)
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
