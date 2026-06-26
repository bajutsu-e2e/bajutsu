"""ClaudeCodeAgent — the authoring agent backed by the Claude Code CLI (`claude`).

Same `Agent` protocol as `ClaudeAgent`, but instead of the pay-per-token Anthropic API it
shells out to a local `claude -p` (print mode) call. With the CLI authenticated by a Claude
Pro/Max subscription (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`), authoring draws on
the subscription quota rather than API credits.

The forced single tool call is expressed as a JSON-Schema **structured output** (`--json-schema`):
the CLI returns a result envelope whose `structured_output` field is the schema-conformant
action object, which `proposal_from_call` maps to a scenario step exactly as the API agent's
tool_use block does.

Caveats vs the API agent: this path is text-only (the screenshot is not sent — the agent
reasons from the accessibility element list), and `ANTHROPIC_API_KEY`, if set, overrides the
subscription and forces API billing (the CLI's auth precedence).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from typing import Any

from bajutsu.agent import Observation, Proposal
from bajutsu.claude_agent import (
    PLAN_SYSTEM,
    SYSTEM_PROMPT,
    _render,
    proposal_from_call,
    steps_from_plan,
)

# The selector fragment shared by every action (mirrors the API agent's tool inputs).
_TARGET_SCHEMA: dict[str, Any] = {
    "id": {"type": "string"},
    "label": {"type": "string"},
    "value": {"type": "string"},
    "traits": {"type": "array", "items": {"type": "string"}},
    "index": {"type": "integer"},
}

# One action per turn, as a single object. `tool` picks the kind; the other fields are filled
# per kind (selector for tap; selector + text for type_text; selector + timeout for wait_for;
# assertions for finish). The CLI enforces this shape via --json-schema.
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # `reason` first so the model states its thinking before committing to an action.
        "reason": {
            "type": "string",
            "description": "one short sentence of your reasoning for this turn: what you see and "
            "why this action advances the goal",
        },
        "tool": {"type": "string", "enum": ["tap", "type_text", "wait_for", "finish"]},
        **_TARGET_SCHEMA,
        "text": {"type": "string", "description": "text to type (type_text)"},
        "timeout": {"type": "number", "description": "seconds to wait (wait_for)"},
        "assertions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **_TARGET_SCHEMA,
                    "check": {
                        "type": "string",
                        "enum": ["exists", "notExists", "valueEquals", "labelContains"],
                    },
                    "text": {"type": "string"},
                },
                "required": ["check"],
            },
        },
    },
    "required": ["reason", "tool"],
}

_STRUCTURED_NOTE = (
    "\n\nYou are running non-interactively with no screenshot — reason only from the element "
    "list above. Do not use any tools or read any files. Emit exactly one action object: first "
    "set `reason` to one sentence of your reasoning (what you see and why this action advances "
    "the goal — this is shown live to the person watching), then set `tool` to tap, type_text, "
    "wait_for, or finish and fill its fields (the target via id/label/value/traits/index; "
    "`text` for type_text; `timeout` for wait_for; `assertions` for finish)."
)

# The ordered, human-readable decomposition of the goal, emitted before the run starts.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
    "required": ["steps"],
}

_PLAN_NOTE = (
    "\n\nYou are running non-interactively. Do not use any tools or read any files. Emit exactly "
    "one object whose `steps` is the ordered list of concrete, plain-language steps."
)

# A runner takes the argv and returns the CLI's stdout. Injectable for tests.
Runner = Callable[[list[str]], str]


def _subscription_env() -> dict[str, str]:
    """The child env with ANTHROPIC_API_KEY stripped.

    The CLI's auth precedence puts an API key above the subscription OAuth token, so leaving
    the key in (bajutsu loads it from .env into the process) would silently bill the API —
    the very thing the Claude Code agent avoids.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def _default_runner(cmd: list[str]) -> str:
    # Run from a scratch cwd so the CLI does not load this repo's CLAUDE.md / skills / MCP
    # into the authoring call. Auth and the user-level config still apply.
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=tempfile.gettempdir(),
            env=_subscription_env(),
            timeout=180,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "`claude` CLI not found — install Claude Code, or use the API agent (--agent api)."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


class ClaudeCodeAgent:
    """Agent implementation that asks the Claude Code CLI for the next action via structured output."""

    def __init__(
        self, model: str | None = None, runner: Runner | None = None, binary: str = "claude"
    ) -> None:
        self._model = model
        self._runner = runner or _default_runner
        self._binary = binary

    def _command(self, prompt: str, schema: dict[str, Any], system: str) -> list[str]:
        cmd = [
            self._binary,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--append-system-prompt",
            system,
        ]
        if self._model:
            cmd += ["--model", self._model]
        cmd.append(prompt)
        return cmd

    def next_action(self, observation: Observation) -> Proposal:
        cmd = self._command(_render(observation), PROPOSAL_SCHEMA, SYSTEM_PROMPT + _STRUCTURED_NOTE)
        stdout = self._runner(cmd)
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"claude -p returned non-JSON output: {stdout[:200]!r}") from exc
        if envelope.get("is_error"):
            raise RuntimeError(f"claude -p reported an error: {envelope.get('result')!r}")
        out = envelope.get("structured_output")
        if not isinstance(out, dict) or not out.get("tool"):
            return Proposal(done=True, note="claude returned no structured action")
        return proposal_from_call(out["tool"], out)

    def plan(self, goal: str) -> list[str]:
        cmd = self._command(f"Goal: {goal}", PLAN_SCHEMA, PLAN_SYSTEM + _PLAN_NOTE)
        stdout = self._runner(cmd)
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return []  # planning is best-effort context, not required to record
        out = envelope.get("structured_output")
        if envelope.get("is_error") or not isinstance(out, dict):
            return []
        return steps_from_plan(out.get("steps"))
