"""Contract tests for the vendor-neutral AI seam (BE-0104).

Exercise the seam with an *in-process fake adapter* (no network, no device — runs in the fast
Linux gate): the registry dispatches provider name → adapter, an unknown provider fails closed, and
redaction runs before any adapter is reached. Also covers the neutral response helper and the
built-in Anthropic registration.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import FakeBlock

from bajutsu.agent import Observation
from bajutsu.ai import base, create_backend, credential_gap, known_providers
from bajutsu.ai.base import (
    AnyTool,
    Message,
    MessageRequest,
    MessageResponse,
    TextBlock,
    TextPart,
    ToolDef,
    ToolUseBlock,
)
from bajutsu.ai.registry import Adapter, register
from bajutsu.anthropic_client import AiConfig
from bajutsu.claude_agent import ClaudeAgent
from bajutsu.config import AiSettings
from bajutsu.drivers import base as drivers_base
from bajutsu.redaction import Redactor
from bajutsu.scenario import Redact


class RecordingBackend:
    """An in-process `AiBackend` that records requests and returns a scripted tool call."""

    def __init__(self, tool_use: FakeBlock | None = None) -> None:
        self.requests: list[MessageRequest] = []
        self._tool_use = tool_use

    def create_message(self, request: MessageRequest) -> MessageResponse:
        self.requests.append(request)
        content = (
            [ToolUseBlock(name=self._tool_use.name, input=self._tool_use.input)]
            if self._tool_use is not None
            else []
        )
        return MessageResponse(content=content)


@pytest.fixture
def fake_provider() -> Iterator[RecordingBackend]:
    """Register a fake provider `test-fake` for the test, then remove it (global registry)."""
    from bajutsu.ai import registry

    backend = RecordingBackend(FakeBlock("tap", {"id": "a"}))
    register("test-fake", Adapter(factory=lambda ai: backend, credential_gap=lambda ai: None))
    try:
        yield backend
    finally:
        registry._ADAPTERS.pop("test-fake", None)


# --- neutral response helper ---


def test_first_tool_use_finds_the_tool_block_past_text() -> None:
    response = MessageResponse(
        content=[TextBlock(text="thinking"), ToolUseBlock(name="do", input={"k": 1})]
    )
    tool_use = response.first_tool_use()
    assert tool_use is not None and tool_use.name == "do" and tool_use.input == {"k": 1}


def test_first_tool_use_is_none_without_a_tool_block() -> None:
    assert MessageResponse(content=[TextBlock(text="just text")]).first_tool_use() is None
    assert MessageResponse(content=[]).first_tool_use() is None


# --- built-in Anthropic registration ---


def test_builtin_providers_are_registered() -> None:
    assert {"anthropic", "bedrock"} <= set(known_providers())


def test_create_backend_defaults_to_the_anthropic_adapter() -> None:
    from bajutsu.ai.anthropic import AnthropicBackend

    assert isinstance(create_backend(), AnthropicBackend)
    assert isinstance(create_backend(AiConfig(provider="bedrock")), AnthropicBackend)


def test_credential_gap_dispatches_to_the_resolved_provider(monkeypatch: Any) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Anthropic without a key, and Bedrock without a model id, each report their own gap token.
    assert credential_gap(AiConfig(provider="anthropic")) == "anthropic-key"
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    assert credential_gap(AiConfig(provider="bedrock")) == "bedrock-model"


# --- the registry is a real extension point (in-process fake adapter) ---


def test_registered_adapter_is_dispatched(fake_provider: RecordingBackend) -> None:
    ai = AiConfig(provider="test-fake")
    assert create_backend(ai) is fake_provider
    assert credential_gap(ai) is None
    assert "test-fake" in known_providers()


def test_tool_use_loop_drives_the_neutral_interface(fake_provider: RecordingBackend) -> None:
    # A call site (the record agent) reaches the fake adapter purely through the neutral seam.
    agent = ClaudeAgent(ai=AiConfig(provider="test-fake"))
    el: drivers_base.Element = {
        "identifier": "a",
        "label": "A",
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }
    proposal = agent.next_action(Observation(goal="g", screen=[el], history=[]))
    assert proposal.step is not None and proposal.step.tap is not None
    request = fake_provider.requests[0]
    assert isinstance(request, MessageRequest)
    assert isinstance(request.tool_choice, AnyTool)
    assert {t.name for t in request.tools} == {"tap", "type_text", "wait_for", "finish"}
    assert all(isinstance(t, ToolDef) for t in request.tools)


def test_completion_and_vision_shapes_reach_the_adapter(fake_provider: RecordingBackend) -> None:
    # A single-shot request carrying a text part is what the completion / classification paths send.
    backend = create_backend(AiConfig(provider="test-fake"))
    backend.create_message(
        MessageRequest(
            system="s",
            messages=[Message(role="user", content=[TextPart(text="classify this")])],
            tools=[ToolDef(name="c", description="c", input_schema={"type": "object"})],
            tool_choice=AnyTool(),
            model="m",
            max_tokens=16,
        )
    )
    content = fake_provider.requests[0].messages[0].content
    assert [type(p) for p in content] == [TextPart]


# --- fail closed on an unknown provider (config validation) ---


def test_unknown_provider_fails_closed_at_config_load() -> None:
    with pytest.raises(ValueError, match=r"unknown ai\.provider"):
        AiSettings(provider="no-such-provider")


def test_registered_provider_passes_config_validation(fake_provider: RecordingBackend) -> None:
    assert AiSettings(provider="test-fake").provider == "test-fake"


# --- redaction runs before any adapter is reached ---


def test_redaction_happens_before_the_adapter(fake_provider: RecordingBackend) -> None:
    redactor = Redactor(Redact(), values=["sk-secret-token"])
    agent = ClaudeAgent(ai=AiConfig(provider="test-fake"), redactor=redactor)
    el: drivers_base.Element = {
        "identifier": "tok",
        "label": "token: sk-secret-token",
        "traits": ["staticText"],
        "value": "sk-secret-token",
        "frame": (0.0, 0.0, 1.0, 1.0),
    }
    agent.next_action(Observation(goal="g", screen=[el], history=[]))
    text = next(
        p.text for p in fake_provider.requests[0].messages[0].content if isinstance(p, TextPart)
    )
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


def test_module_reexports_the_neutral_types() -> None:
    # The package surface is Bajutsu's own types, not a vendor SDK re-export.
    assert base.MessageRequest is MessageRequest
    assert base.AiBackend.__module__ == "bajutsu.ai.base"
