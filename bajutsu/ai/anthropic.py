"""Anthropic reference adapter for the vendor-neutral AI seam (BE-0104).

The first adapter behind `bajutsu.ai.base.AiBackend`. It wraps the existing
`anthropic_client.make_client`, so the Anthropic API *and* Amazon Bedrock both stay covered by this
one adapter (Bedrock is an Anthropic-SDK variant — BE-0053). It translates a neutral
`MessageRequest` into an Anthropic Messages API call and the Anthropic reply back into neutral
content blocks; behavior is unchanged from the pre-BE-0104 call sites (the model id is resolved by
the caller with `resolve_model`). This adapter also owns the Anthropic-family `credential_gap`
(BE-0047 / BE-0053 / BE-0163), which `bajutsu.ai.registry.credential_gap` dispatches to (BE-0246).
"""

from __future__ import annotations

import base64
import os
from typing import Any

from bajutsu.agents.ai_config import BEDROCK_MODEL_ENV, AiConfig, resolve_provider
from bajutsu.agents.anthropic_client import ant_credential_gap, key_env, make_client
from bajutsu.ai.base import (
    AnyTool,
    ContentBlock,
    ContentPart,
    ImagePart,
    MessageRequest,
    MessageResponse,
    TextBlock,
    ToolUseBlock,
)


def _part(part: ContentPart) -> dict[str, Any]:
    """Translate a neutral content part into an Anthropic content block."""
    if isinstance(part, ImagePart):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": part.media_type,
                "data": base64.standard_b64encode(part.data).decode("ascii"),
            },
        }
    return {"type": "text", "text": part.text}


def _block(block: Any) -> ContentBlock | None:
    """Translate one Anthropic response block into a neutral block, dropping anything else."""
    if block.type == "tool_use":
        return ToolUseBlock(name=block.name, input=block.input)
    if block.type == "text":
        return TextBlock(text=block.text)
    return None


class AnthropicBackend:
    """`AiBackend` over the Anthropic SDK (Anthropic API or Bedrock, via `make_client`).

    ``client`` short-circuits the factory — the injection seam the adapter's tests use. The static
    system prompt is sent as a single prompt-cached text block, matching every pre-BE-0104 path.
    """

    def __init__(self, *, ai: AiConfig | None = None, client: Any = None) -> None:
        self._ai = ai
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = make_client(ai=self._ai)
        return self._client

    def create_message(self, request: MessageRequest) -> MessageResponse:
        tool_choice: dict[str, Any] = (
            {"type": "any"}
            if isinstance(request.tool_choice, AnyTool)
            else {"type": "tool", "name": request.tool_choice.name}
        )
        message = self._ensure_client().messages.create(
            model=request.model,
            max_tokens=request.max_tokens,
            system=[
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ],
            tool_choice=tool_choice,
            messages=[
                {"role": m.role, "content": [_part(p) for p in m.content]} for m in request.messages
            ],
        )
        content = [b for b in (_block(b) for b in message.content) if b is not None]
        return MessageResponse(
            content=content,
            stop_reason=getattr(message, "stop_reason", None),
            usage=getattr(message, "usage", None),
        )


def factory(ai: AiConfig | None = None) -> AnthropicBackend:
    """Build the Anthropic backend — the registry's adapter factory for `anthropic` / `bedrock`."""
    return AnthropicBackend(ai=ai)


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """What the Anthropic-family provider is missing to authenticate, or ``None`` when it can.

    The Anthropic adapter's half of the BE-0047 credential check, dispatched to per provider by
    `bajutsu.ai.registry.credential_gap` (the single public entry point; `claude-code` has its own).
    The ``api-key`` provider needs the key named by ``ai.keyEnv`` (default ``ANTHROPIC_API_KEY``);
    Bedrock authenticates with the standard AWS credential chain (env / shared profile / instance or
    task role — resolved by the SDK, not checked here) and needs a provider-prefixed model id
    instead (``ai.model`` or ``BAJUTSU_BEDROCK_MODEL``), since the bare Anthropic id is not a valid
    Bedrock model id. The ``ant`` provider (BE-0163) needs its CLI installed and signed in — probed
    by `anthropic_client.ant_credential_gap`. Returns ``"anthropic-key"`` / ``"bedrock-model"`` /
    ``"ant-cli-missing"`` / ``"ant-cli-unauthenticated"`` so callers can phrase a
    provider-appropriate message.
    """
    prov = resolve_provider(ai)
    if prov == "bedrock":
        has_model = (ai and ai.model) or os.environ.get(BEDROCK_MODEL_ENV)
        return None if has_model else "bedrock-model"
    if prov == "ant":
        return ant_credential_gap()
    return None if os.environ.get(key_env(ai)) else "anthropic-key"
