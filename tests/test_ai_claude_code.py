"""The Claude Code CLI adapter turns a neutral request into a `claude -p` call (BE-0176).

Exercises the adapter with an injected runner (no subprocess, runs in the fast Linux gate): the
forced tool call maps to `--json-schema` structured output (single tool straight through, multi-tool
via the discriminator wrapper), images are written to a scratch dir and named in the prompt with
`Read` scoped to that dir, text-only turns allow no tool, and malformed / error envelopes are
handled — plus the subscription env strips the API key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from bajutsu.ai import claude_code
from bajutsu.ai.base import (
    AnyTool,
    ImagePart,
    Message,
    MessageRequest,
    NamedTool,
    TextPart,
    ToolChoice,
    ToolDef,
)

_ANY: ToolChoice = AnyTool()  # module-level singleton (ruff B008: no call in an argument default)
_DO = ToolDef(name="do", description="does a thing", input_schema={"type": "object"})
_TAP = ToolDef(name="tap", description="tap", input_schema={"type": "object"})
_FINISH = ToolDef(name="finish", description="finish", input_schema={"type": "object"})


def _request(
    *,
    tools: list[ToolDef] | None = None,
    tool_choice: ToolChoice = _ANY,
    image: bytes | None = None,
) -> MessageRequest:
    content: list[Any] = []
    if image is not None:
        content.append(ImagePart(data=image))
    content.append(TextPart(text="observe this"))
    return MessageRequest(
        system="SYS",
        messages=[Message(role="user", content=content)],
        tools=tools or [_DO],
        tool_choice=tool_choice,
        model="claude-opus-4-8",
        max_tokens=256,
    )


class FakeRunner:
    """Records the argv + cwd, optionally runs a probe against the scratch dir, returns an envelope."""

    def __init__(self, envelope: dict[str, Any], *, probe: Any = None) -> None:
        self._stdout = json.dumps(envelope)
        self._probe = probe
        self.cmd: list[str] = []
        self.cwd: str = ""

    def __call__(self, cmd: list[str], cwd: str) -> str:
        self.cmd, self.cwd = cmd, cwd
        if self._probe is not None:
            self._probe(cmd, cwd)
        return self._stdout


def _flag(cmd: list[str], name: str) -> str:
    """The value following *name* in the argv."""
    return cmd[cmd.index(name) + 1]


def _envelope(structured_output: Any) -> dict[str, Any]:
    return {"is_error": False, "structured_output": structured_output}


def test_named_tool_uses_its_schema_and_attributes_the_result() -> None:
    runner = FakeRunner(_envelope({"k": "v"}))
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tool_choice=NamedTool(name="do"))
    )
    assert json.loads(_flag(runner.cmd, "--json-schema")) == {"type": "object"}
    tool_use = resp.first_tool_use()
    assert tool_use is not None and tool_use.name == "do" and tool_use.input == {"k": "v"}


def test_single_anytool_is_treated_as_the_named_case() -> None:
    runner = FakeRunner(_envelope({"id": "a"}))
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tools=[_DO], tool_choice=AnyTool())
    )
    tool_use = resp.first_tool_use()
    assert tool_use is not None and tool_use.name == "do" and tool_use.input == {"id": "a"}


def test_multi_anytool_wraps_the_schema_and_unwraps_the_choice() -> None:
    runner = FakeRunner(_envelope({"tool": "finish", "arguments": {"note": "done"}}))
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tools=[_TAP, _FINISH], tool_choice=AnyTool())
    )
    schema = json.loads(_flag(runner.cmd, "--json-schema"))
    assert schema["properties"]["tool"]["enum"] == ["tap", "finish"]
    assert "arguments" in schema["properties"]
    tool_use = resp.first_tool_use()
    assert tool_use is not None and tool_use.name == "finish" and tool_use.input == {"note": "done"}


@pytest.mark.parametrize("output", [None, "not-a-dict", {"tool": "finish"}, {"arguments": {}}])
def test_malformed_structured_output_yields_no_tool_use(output: Any) -> None:
    runner = FakeRunner(_envelope(output))
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tools=[_TAP, _FINISH], tool_choice=AnyTool())
    )
    assert resp.first_tool_use() is None


def test_image_is_written_named_in_prompt_and_read_allowed_then_cleaned_up() -> None:
    seen: dict[str, Any] = {}

    def probe(cmd: list[str], cwd: str) -> None:
        # The scratch dir exists during the call and holds the PNG the prompt points at.
        pngs = list(Path(cwd).glob("*.png"))
        seen["png_bytes"] = pngs[0].read_bytes()
        seen["prompt_names_png"] = str(pngs[0]) in cmd[-1]

    runner = FakeRunner(_envelope({"k": "v"}), probe=probe)
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tool_choice=NamedTool(name="do"), image=b"\x89PNG\r\n\x1a\n bytes")
    )
    assert seen["png_bytes"] == b"\x89PNG\r\n\x1a\n bytes"
    assert seen["prompt_names_png"]
    assert _flag(runner.cmd, "--add-dir") == runner.cwd
    assert _flag(runner.cmd, "--allowedTools") == "Read"
    assert "Read" not in _flag(runner.cmd, "--disallowedTools")
    assert resp.first_tool_use() is not None
    # The per-call scratch dir is removed once the call returns.
    assert not os.path.exists(runner.cwd)


def test_text_only_turn_allows_no_tool_and_writes_no_file() -> None:
    def probe(cmd: list[str], cwd: str) -> None:
        assert list(Path(cwd).glob("*.png")) == []

    runner = FakeRunner(_envelope({"k": "v"}), probe=probe)
    claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tool_choice=NamedTool(name="do"))
    )
    assert "--add-dir" not in runner.cmd
    assert "--allowedTools" not in runner.cmd
    assert "Read" in _flag(runner.cmd, "--disallowedTools").split(",")


def test_model_and_fail_closed_permission_mode_pass_through() -> None:
    runner = FakeRunner(_envelope({"k": "v"}))
    claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tool_choice=NamedTool(name="do"))
    )
    assert _flag(runner.cmd, "--model") == "claude-opus-4-8"
    assert _flag(runner.cmd, "--permission-mode") == "default"
    assert _flag(runner.cmd, "--append-system-prompt").startswith("SYS")


def test_usage_passes_through_and_is_error_and_non_json_raise() -> None:
    runner = FakeRunner({"is_error": False, "structured_output": {"k": "v"}, "usage": {"in": 1}})
    resp = claude_code.ClaudeCodeBackend(runner=runner).create_message(
        _request(tool_choice=NamedTool(name="do"))
    )
    assert resp.usage == {"in": 1}

    err = FakeRunner({"is_error": True, "result": "boom"})
    with pytest.raises(RuntimeError, match="reported an error"):
        claude_code.ClaudeCodeBackend(runner=err).create_message(
            _request(tool_choice=NamedTool(name="do"))
        )

    def junk(cmd: list[str], cwd: str) -> str:
        return "not json"

    with pytest.raises(RuntimeError, match="non-JSON"):
        claude_code.ClaudeCodeBackend(runner=junk).create_message(
            _request(tool_choice=NamedTool(name="do"))
        )


def test_child_env_strips_the_api_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    env = claude_code._child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "PATH" in env  # the rest of the environment is preserved
