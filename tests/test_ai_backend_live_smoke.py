"""Real-API contract smoke for the vendor-neutral AI backend adapters (BE-0300).

Every other test of these adapters drives them through `FakeAnthropic` / `FakeBlock` — a response
shaped exactly as the fake's author believes the real service returns, never one the API actually
produced. These key-gated smoke tests close that gap: they call a real provider (the direct Anthropic
API, Amazon Bedrock, or the `ant` CLI) with a trivial forced-tool prompt and assert only that the
adapter's translation lands as a populated, parseable neutral `MessageResponse` — a transport-and-
schema check, never a claim about what the model chose to say.

They are signal-first, not a gate (the BE-0282 precedent): each is deselected from the fast suite by
the `live` marker (pyproject `addopts` `not live`, opt in with `-m live`) *and* skipped whenever its
provider has no credential, so `make check` stays hermetic even when a contributor's `ANTHROPIC_API_KEY`
is exported for `record` / `triage --ai`. No LLM ever touches the `run` / CI verdict (prime directive
1): these exercise the AI *authoring* periphery alone, on a manual `workflow_dispatch` lane.

The deterministic self-checks below (driven by `FakeAnthropic`, unmarked so they run in the gate) keep
the wire-contract assertion honest, so a live run genuinely validates rather than passing vacuously.
"""

from __future__ import annotations

import pytest
from conftest import FakeAnthropic, FakeBlock

from bajutsu.agents.ai_config import AiConfig, resolve_model
from bajutsu.ai import create_backend, credential_gap
from bajutsu.ai.anthropic import AnthropicBackend
from bajutsu.ai.base import (
    Message,
    MessageRequest,
    MessageResponse,
    NamedTool,
    TextPart,
    ToolDef,
    ToolUseBlock,
)

# The cheapest current model — the smoke proves plumbing, not model quality, so a handful of tokens is
# enough. On Bedrock `resolve_model` swaps in `BAJUTSU_BEDROCK_MODEL` (a provider-prefixed id) instead.
_SMOKE_MODEL = "claude-haiku-4-5-20251001"

# The one tool the smoke offers; a forced `tool_choice` makes a compliant response carry a tool-use
# block, so the contract check has something to parse without judging the model's word choice.
_ECHO_TOOL = ToolDef(
    name="echo",
    description="Echo the given value straight back.",
    input_schema={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
)


def _request(model: str = _SMOKE_MODEL) -> MessageRequest:
    """A minimal forced-tool turn — a trivial prompt, one tool, a named `tool_choice`, few tokens."""
    return MessageRequest(
        system="You are a test harness. Call the tool you are given.",
        messages=[
            Message(role="user", content=[TextPart(text="Call the echo tool with any value.")])
        ],
        tools=[_ECHO_TOOL],
        tool_choice=NamedTool(name="echo"),
        model=model,
        max_tokens=64,
    )


def _assert_wire_contract(response: MessageResponse) -> None:
    """The real response arrived as a populated, parseable neutral `MessageResponse` (BE-0300).

    A wire-contract check, not a model-quality one: the adapter's translation produced at least one
    neutral content block and — since the smoke forces a tool call — a `ToolUseBlock` carrying a tool
    name and a dict input. What the model put *in* that input is deliberately never asserted.
    """
    assert response.content, f"adapter returned no content blocks: {response}"
    tool_use = response.first_tool_use()
    assert tool_use is not None, f"forced tool_choice produced no tool-use block: {response}"
    assert tool_use.name, f"tool-use block has no tool name: {tool_use}"
    assert isinstance(tool_use.input, dict), f"tool-use input is not an object: {tool_use}"


# --- Deterministic self-checks (run in the gate, keep the contract assertion honest) -------------


def test_wire_contract_accepts_a_populated_tool_use_response() -> None:
    response = AnthropicBackend(
        client=FakeAnthropic(FakeBlock("echo", {"value": "hi"}))
    ).create_message(_request())
    _assert_wire_contract(response)  # a populated tool-use response satisfies the contract


def test_wire_contract_rejects_an_empty_response() -> None:
    response = AnthropicBackend(client=FakeAnthropic()).create_message(_request())  # no blocks
    with pytest.raises(AssertionError):
        _assert_wire_contract(response)


# --- Key-gated live smoke (real provider) --------------------------------------------------------


def _requires_credential(provider: str) -> pytest.MarkDecorator:
    """Skip unless *provider* can authenticate — `credential_gap` returns its missing reason or None."""
    gap = credential_gap(AiConfig(provider=provider))
    return pytest.mark.skipif(
        gap is not None,
        reason=f"real-API smoke is signal-first (BE-0300); {provider} unavailable: {gap}",
    )


def _run_live_smoke(provider: str) -> None:
    """Drive the real *provider*'s adapter once and assert its wire contract (never model quality)."""
    ai = AiConfig(provider=provider)
    backend = create_backend(ai)
    response = backend.create_message(_request(resolve_model(_SMOKE_MODEL, ai)))
    _assert_wire_contract(response)
    assert isinstance(response.first_tool_use(), ToolUseBlock)


@pytest.mark.live
@_requires_credential("api-key")
def test_direct_anthropic_api_adapter_wire_contract() -> None:
    _run_live_smoke("api-key")


@pytest.mark.live
@_requires_credential("bedrock")
def test_bedrock_adapter_wire_contract() -> None:
    _run_live_smoke("bedrock")


@pytest.mark.live
@_requires_credential("ant")
def test_ant_cli_adapter_wire_contract() -> None:
    _run_live_smoke("ant")
