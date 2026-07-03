"""Normalized request / response types and the `AiBackend` protocol (BE-0104).

The seam describes *only* what Bajutsu's AI paths actually ask of a model, and nothing more (the
BE-0104 capability audit): one forced-tool `create_message` turn carrying a system prompt, a user
message of text and/or images, and tool definitions; a response of text and tool-use content
blocks. No streaming and no multi-turn `tool_result` feedback — no current path uses them (the
`record` loop drives many single-shot turns, not one turn with tool results fed back).

These types are Bajutsu's own, deliberately *not* re-exports of any vendor SDK, so no call site
depends on a provider's message / tool / image shape. An adapter (see `bajutsu.ai.anthropic`)
translates them to and from a concrete provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class TextPart:
    """A text part of a user message."""

    text: str


@dataclass(frozen=True)
class ImagePart:
    """An image part of a user message — raw bytes plus its media type.

    Held as raw bytes (not base64) so the neutral layer never carries a vendor's encoding; the
    adapter encodes as the provider requires. Images cannot be redacted (BE-0047), so they reach
    only the user-configured endpoint unchanged.
    """

    data: bytes
    media_type: str = "image/png"


ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    """One conversation message. Every current path sends a single ``user`` message."""

    role: str
    content: list[ContentPart]


@dataclass(frozen=True)
class ToolDef:
    """A tool the model may call: its name, description, and JSON-schema input shape."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class AnyTool:
    """Force the model to call exactly one of the offered tools (any of them)."""


@dataclass(frozen=True)
class NamedTool:
    """Force the model to call the one named tool."""

    name: str


# Every current path forces a tool call — either "call some tool" or "call this tool". Free-choice
# (optional tool use) is not modeled because no path uses it.
ToolChoice = AnyTool | NamedTool


@dataclass(frozen=True)
class MessageRequest:
    """A single normalized turn to send to a backend.

    ``system`` is a plain string; the adapter applies whatever prompt-caching the provider offers
    (all paths cache the static system prompt today). ``tool_choice`` forces a tool call, so a
    compliant response carries a tool-use block — callers still check
    ``MessageResponse.first_tool_use()`` for the rare case a model doesn't comply.
    """

    system: str
    messages: list[Message]
    tools: list[ToolDef]
    tool_choice: ToolChoice
    model: str
    max_tokens: int


@dataclass(frozen=True)
class TextBlock:
    """A text block in a model response."""

    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """A tool-use request in a model response: the tool name and its argument object."""

    name: str
    input: dict[str, Any]


ContentBlock = TextBlock | ToolUseBlock


@dataclass(frozen=True)
class MessageResponse:
    """A normalized model response.

    ``usage`` is the provider's own token-accounting object, passed through untouched so
    `bajutsu.usage.record` reads it exactly as before (reporting only — never on the verdict path).
    """

    content: list[ContentBlock]
    stop_reason: str | None = None
    usage: Any = None

    def first_tool_use(self) -> ToolUseBlock | None:
        """The first tool-use block, or ``None`` when the model returned no tool call."""
        return next((b for b in self.content if isinstance(b, ToolUseBlock)), None)


class AiBackend(Protocol):
    """A model provider behind one interface (BE-0104).

    An adapter implements this to translate a `MessageRequest` into a concrete provider's API and
    the provider's reply back into a `MessageResponse`. The only method the AI paths need is a
    single forced-tool turn.
    """

    def create_message(self, request: MessageRequest) -> MessageResponse: ...
