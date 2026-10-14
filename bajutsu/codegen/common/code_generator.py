"""The seam each codegen target implements: the target-specific parts of a generated test file."""

from __future__ import annotations

from typing import Protocol

from bajutsu.common.scenario import Assertion, CleanupRule, Scenario, Step

from .cleanup_emission import CleanupEmission


class CodeGenerator(Protocol):
    """The target-specific parts of a generated test file.

    The shared walk supplies the structure (scenario loop, env merge, body indentation, the
    expect divider); a generator supplies only the syntax of each line for its target language.
    """

    def file_preamble(self) -> list[str]:
        """The lines before the first scenario (header comment, imports, class/describe open)."""

    def scenario_open(self, name: str) -> str:
        """The line opening one scenario's test function/case (carries its own indent)."""

    def cleanup_lines(self, cleanup: list[CleanupRule]) -> CleanupEmission:
        """How this target renders the scenario's `cleanup` rules (BE-0392).

        Called even for an empty list, so a target that always needs a wrapper can say so; the
        default `CleanupEmission()` renders nothing and leaves the body where it was.
        """

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
