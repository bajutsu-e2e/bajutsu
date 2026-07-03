"""Tests for ClaudeCodeAgent with an injected fake `claude -p` runner (no real CLI / API)."""

from __future__ import annotations

import json
from typing import Any

from bajutsu.agent import Observation
from bajutsu.claude_code_agent import PLAN_SCHEMA, PROPOSAL_SCHEMA, ClaudeCodeAgent
from bajutsu.drivers import base


def _el(label: str | None, traits: list[str], value: str | None = None) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": traits,
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _obs(goal: str = "g") -> Observation:
    return Observation(goal=goal, screen=[_el("Get Started", ["button"])], history=[])


def _runner_returning(structured: dict[str, Any] | None, *, is_error: bool = False):
    """A fake runner that echoes back a CLI result envelope, and records the argv it saw."""
    seen: list[list[str]] = []

    def run(cmd: list[str]) -> str:
        seen.append(cmd)
        return json.dumps(
            {
                "type": "result",
                "is_error": is_error,
                "result": "ok",
                "structured_output": structured,
            }
        )

    run.seen = seen  # type: ignore[attr-defined]
    return run


def test_tap_by_label_structured_output() -> None:
    runner = _runner_returning({"tool": "tap", "label": "Get Started"})
    step = ClaudeCodeAgent(runner=runner).next_action(_obs()).step
    assert step is not None and step.tap is not None
    assert step.tap.label == "Get Started" and step.tap.id is None


def test_type_by_value_and_traits() -> None:
    runner = _runner_returning(
        {"tool": "type_text", "value": "Email", "traits": ["textField"], "text": "a@b.co"}
    )
    step = ClaudeCodeAgent(runner=runner).next_action(_obs()).step
    assert step is not None and step.type is not None and step.type.into is not None
    assert step.type.into.value == "Email" and step.type.text == "a@b.co"


def test_finish_label_contains() -> None:
    runner = _runner_returning(
        {
            "tool": "finish",
            "assertions": [{"label": "Count: 2", "check": "labelContains", "text": "2"}],
        }
    )
    proposal = ClaudeCodeAgent(runner=runner).next_action(_obs())
    assert proposal.done is True
    assert proposal.expect[0].label is not None
    assert proposal.expect[0].label.sel.label == "Count: 2"


def test_no_structured_output_finishes_gracefully() -> None:
    proposal = ClaudeCodeAgent(runner=_runner_returning(None)).next_action(_obs())
    assert proposal.done is True and proposal.step is None


def test_plan_decomposes_goal() -> None:
    runner = _runner_returning({"steps": ["Tap Get Started", "", "Confirm home"]})
    steps = ClaudeCodeAgent(runner=runner).plan("sign in")
    assert steps == ["Tap Get Started", "Confirm home"]  # blanks dropped, order kept
    cmd = runner.seen[0]  # type: ignore[attr-defined]
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == PLAN_SCHEMA
    assert "Goal: sign in" in cmd[-1]


def test_plan_tolerates_a_bad_envelope() -> None:
    assert ClaudeCodeAgent(runner=_runner_returning(None)).plan("g") == []
    assert (
        ClaudeCodeAgent(runner=_runner_returning({"steps": ["x"]}, is_error=True)).plan("g") == []
    )


def test_command_passes_schema_and_headless_flags() -> None:
    runner = _runner_returning({"tool": "tap", "label": "Get Started"})
    ClaudeCodeAgent(runner=runner, model="claude-opus-4-8").next_action(_obs())
    cmd = runner.seen[0]  # type: ignore[attr-defined]
    assert cmd[:2] == ["claude", "-p"]
    assert "--json-schema" in cmd and "--output-format" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == PROPOSAL_SCHEMA
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    # the rendered observation (goal + elements) is the final positional prompt
    assert "Get Started" in cmd[-1]


def _assert_tool_restricted(cmd: list[str]) -> None:
    # The denylist covers shell (Bash) + every file read/write tool; pin the exact set so
    # dropping one silently (e.g. Glob/Grep) fails here. Plus a fail-closed permission mode so
    # an unanticipated tool cannot silently proceed in print mode (BE-0125).
    denied = set(cmd[cmd.index("--disallowedTools") + 1].split(","))
    assert denied == {"Bash", "Read", "Write", "Edit", "NotebookEdit", "Glob", "Grep"}
    assert cmd[cmd.index("--permission-mode") + 1] == "default"


def test_next_action_command_is_tool_restricted() -> None:
    runner = _runner_returning({"tool": "tap", "label": "Get Started"})
    ClaudeCodeAgent(runner=runner).next_action(_obs())
    _assert_tool_restricted(runner.seen[0])  # type: ignore[attr-defined]


def test_plan_command_is_tool_restricted() -> None:
    runner = _runner_returning({"steps": ["Tap Get Started"]})
    ClaudeCodeAgent(runner=runner).plan("sign in")
    _assert_tool_restricted(runner.seen[0])  # type: ignore[attr-defined]
