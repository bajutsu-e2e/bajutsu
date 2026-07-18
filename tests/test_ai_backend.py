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

from bajutsu.agents.ai_config import AiConfig
from bajutsu.agents.claude import ClaudeAgent
from bajutsu.agents.protocols import Observation
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
from bajutsu.config import AiSettings
from bajutsu.drivers import base as drivers_base
from bajutsu.evidence.redaction import Redactor
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
    assert {"api-key", "bedrock", "ant", "claude-code"} <= set(known_providers())


def test_builtins_survive_an_adapter_registered_first() -> None:
    # A third-party/test adapter registering before first use must not suppress the built-ins
    # (the guard keys on the built-in names, not on the registry being non-empty).
    from bajutsu.ai import registry

    saved = dict(registry._ADAPTERS)
    sentinel = Adapter(factory=lambda ai: object(), credential_gap=lambda ai: None)  # type: ignore[arg-type,return-value]
    registry._ADAPTERS.clear()
    try:
        register("test-first", sentinel)
        register("api-key", sentinel)  # an explicit registration for a built-in name
        providers = set(known_providers())
        assert {"api-key", "bedrock", "ant", "test-first"} <= providers
        # setdefault leaves the earlier explicit `api-key` registration intact.
        assert registry._ADAPTERS["api-key"] is sentinel
    finally:
        registry._ADAPTERS.clear()
        registry._ADAPTERS.update(saved)


def test_create_backend_defaults_to_the_anthropic_adapter() -> None:
    from bajutsu.ai.anthropic import AnthropicBackend

    assert isinstance(create_backend(), AnthropicBackend)
    assert isinstance(create_backend(AiConfig(provider="bedrock")), AnthropicBackend)
    # `ant` (BE-0163) shares the same provider-agnostic adapter.
    assert isinstance(create_backend(AiConfig(provider="ant")), AnthropicBackend)


def test_claude_code_resolves_to_its_own_adapter() -> None:
    # `claude-code` (BE-0176) is a separate adapter, not another alias on the shared Anthropic one.
    from bajutsu.ai.anthropic import AnthropicBackend
    from bajutsu.ai.claude_code import ClaudeCodeBackend

    backend = create_backend(AiConfig(provider="claude-code"))
    assert isinstance(backend, ClaudeCodeBackend)
    assert not isinstance(backend, AnthropicBackend)


# --- per-provider startup announcement (BE-0176 follow-up) ---


def test_announcement_is_provider_specific() -> None:
    from bajutsu.ai.registry import announcement

    # The Anthropic SDK (default `api-key`) has no reasoning-effort knob, so its line names only the
    # provider and resolved model — one line, no effort, no auth disclosure.
    default = announcement("claude-opus-4-8")
    assert default == ["🤖 AI: api-key · model claude-opus-4-8"]

    # claude-code overrides `announce`: it honors effort and forces a subscription login, so it adds
    # both the effort and an auth line the generic disclosure omits.
    cc = announcement("claude-opus-4-8", AiConfig(provider="claude-code"))
    assert cc[0] == "🤖 AI: claude-code · model claude-opus-4-8 · effort default"
    assert cc[1].startswith("🔑 auth:")


def test_announce_ai_pushes_each_line_to_the_report_sink() -> None:
    from bajutsu.ai import announce_ai

    lines: list[str] = []
    announce_ai(lines.append, default_model="claude-opus-4-8", ai=AiConfig(provider="claude-code"))
    assert len(lines) == 2 and lines[0].startswith("🤖 AI: claude-code")


def test_credential_gap_dispatches_to_the_resolved_provider(monkeypatch: Any) -> None:
    from bajutsu.agents import anthropic_client

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # api-key without a key, and Bedrock without a model id, each report their own gap token.
    assert credential_gap(AiConfig(provider="api-key")) == "anthropic-key"
    # The legacy `anthropic` name still dispatches (normalized to api-key), not "unknown provider".
    assert credential_gap(AiConfig(provider="anthropic")) == "anthropic-key"
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    assert credential_gap(AiConfig(provider="bedrock")) == "bedrock-model"
    # `ant` (BE-0163) dispatches through the same shared adapter to the CLI credential probe.
    monkeypatch.setattr(anthropic_client.shutil, "which", lambda _exe: None)
    assert credential_gap(AiConfig(provider="ant")) == anthropic_client.ANT_CLI_MISSING
    # `claude-code` (BE-0176) dispatches to its own adapter's gap: the `claude` binary is absent.
    from bajutsu.ai import claude_code

    monkeypatch.setattr(claude_code.shutil, "which", lambda _exe: None)
    assert credential_gap(AiConfig(provider="claude-code")) == claude_code.CLI_MISSING


def test_credential_gap_anthropic_reachable_states(monkeypatch: Any) -> None:
    # The Anthropic adapter's own gap (bajutsu/ai/anthropic.py), reached through the dispatch: with a
    # key it is reachable; a custom `ai.keyEnv` is honored; Bedrock reads reachable once it has a
    # provider-prefixed model id (from config), with no ANTHROPIC_API_KEY (BE-0053 — AWS auth).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BAJUTSU_BEDROCK_MODEL", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert credential_gap(AiConfig(provider="api-key")) is None
    # A custom keyEnv: the named var, not ANTHROPIC_API_KEY, decides the gap.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ai_gw = AiConfig(provider="api-key", key_env="MY_GATEWAY_KEY")
    assert credential_gap(ai_gw) == "anthropic-key"
    monkeypatch.setenv("MY_GATEWAY_KEY", "sk-gw-test")
    assert credential_gap(ai_gw) is None
    # Bedrock with a config model id and no Anthropic key is reachable.
    assert (
        credential_gap(AiConfig(provider="bedrock", model="global.anthropic.claude-opus-4-6-v1"))
        is None
    )


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
    assert {t.name for t in request.tools} == {
        "tap",
        "tap_point",
        "swipe",
        "type_text",
        "wait_for",
        "finish",
        "need_screenshot",
        "ask_human",
    }
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


# --- fail closed on an unknown provider (in the AI layer, not config) ---


def test_unknown_provider_is_accepted_by_config() -> None:
    # The deterministic core (config) must not import the AI provider registry (BE-0112), so it can't
    # validate the name — an unknown provider passes config load and fails closed later, in the AI layer.
    assert AiSettings(provider="no-such-provider").provider == "no-such-provider"


def test_unknown_provider_fails_closed_when_the_ai_path_resolves_it() -> None:
    unknown = AiConfig(provider="no-such-provider")
    with pytest.raises(ValueError, match=r"unknown ai\.provider"):
        create_backend(unknown)
    with pytest.raises(ValueError, match=r"unknown ai\.provider"):
        credential_gap(unknown)


def test_registered_provider_resolves(fake_provider: RecordingBackend) -> None:
    assert AiSettings(provider="test-fake").provider == "test-fake"
    assert create_backend(AiConfig(provider="test-fake")) is fake_provider


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
