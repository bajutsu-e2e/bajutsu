"""Shared scenario walk for the codegen emitters (BE-0083).

XCUITest (`codegen.py`) and Playwright (`codegen_playwright.py`) transpile a scenario the same way
— merge the launch environment, open the test, emit a launch line, emit each step, then the
`expect` block, and close — differing only in the per-line target syntax. That walk lives here
once; each target supplies the variable parts through the `CodeGenerator` protocol, so adding a
third target (e.g. an Android emitter) is the cost of its line syntax alone, not another copy of
the skeleton.

This is a pure, deterministic transform: no AI, no device. The per-line builders (`step_lines` /
`assertion_lines` and the selector/locator helpers behind them) stay in each target's module.
"""

from __future__ import annotations

from typing import Protocol

from bajutsu.scenario import Assertion, Scenario, Step

# Body lines (launch env, launch, steps, the expect block) sit one level inside the test function;
# the structural braces (`scenario_open` / `scenario_close`) carry their own indent. Both targets
# comment in C-style, so the `// expect` divider is shared.
_BODY_INDENT = "    "
_EXPECT_COMMENT = "// expect"


class CodeGenerator(Protocol):
    """The target-specific parts of a generated test file. The shared walk supplies the structure
    (scenario loop, env merge, body indentation, the expect divider); a generator supplies only the
    syntax of each line for its target language."""

    def file_preamble(self) -> list[str]:
        """The lines before the first scenario (header comment, imports, class/describe open)."""
        ...

    def scenario_open(self, name: str) -> str:
        """The line opening one scenario's test function/case (carries its own indent)."""
        ...

    def launch_env_line(self, key: str, value: str) -> str:
        """One launch-environment assignment (un-indented; the walk adds the body indent)."""
        ...

    def launch_line(self) -> str:
        """The line that launches/navigates the app (un-indented)."""
        ...

    def step_lines(self, step: Step) -> list[str]:
        """The lines for one scenario step (un-indented)."""
        ...

    def assertion_lines(self, assertion: Assertion) -> list[str]:
        """The lines for one `expect` assertion (un-indented)."""
        ...

    def scenario_close(self) -> str:
        """The line closing one scenario's test function/case (carries its own indent)."""
        ...

    def file_footer(self) -> list[str]:
        """The lines after the last scenario (class/describe close)."""
        ...


def _scenario_lines(
    scenario: Scenario, app_launch_env: dict[str, str], gen: CodeGenerator
) -> list[str]:
    env = {**app_launch_env, **scenario.preconditions.launch_env}
    body: list[str] = [gen.launch_env_line(k, v) for k, v in env.items()]
    body.append(gen.launch_line())
    body.append("")
    for step in scenario.steps:
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
