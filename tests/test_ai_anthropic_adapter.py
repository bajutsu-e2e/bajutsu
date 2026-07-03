"""The Anthropic reference adapter translates the neutral request to the Anthropic wire shape.

This is BE-0104's *behavior-unchanged* guarantee: the migrated call sites now build a neutral
`MessageRequest`, and the adapter must turn it into exactly the `messages.create(...)` payload the
pre-BE-0104 code sent — system prompt as one ephemeral-cached text block, image blocks as base64,
`tool_choice` as `any` / named, tools as name/description/input_schema dicts — and turn the
Anthropic reply back into neutral content blocks.
"""

from __future__ import annotations

import base64

from conftest import FakeAnthropic, FakeBlock

from bajutsu.ai.anthropic import AnthropicBackend
from bajutsu.ai.base import (
    AnyTool,
    ImagePart,
    Message,
    MessageRequest,
    NamedTool,
    TextBlock,
    TextPart,
    ToolDef,
    ToolUseBlock,
)


def _request(tool_choice: object = AnyTool(), *, image: bytes | None = None) -> MessageRequest:
    content: list[object] = []
    if image is not None:
        content.append(ImagePart(data=image))
    content.append(TextPart(text="hello"))
    return MessageRequest(
        system="SYS",
        messages=[Message(role="user", content=content)],  # type: ignore[arg-type]
        tools=[ToolDef(name="do", description="does", input_schema={"type": "object"})],
        tool_choice=tool_choice,  # type: ignore[arg-type]
        model="claude-opus-4-8",
        max_tokens=256,
    )


def test_system_prompt_is_one_ephemeral_cached_text_block() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request())
    system = client.calls[0]["system"]
    assert system == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]


def test_model_and_max_tokens_pass_through() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request())
    assert client.calls[0]["model"] == "claude-opus-4-8"
    assert client.calls[0]["max_tokens"] == 256


def test_tools_become_name_description_schema_dicts() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request())
    assert client.calls[0]["tools"] == [
        {"name": "do", "description": "does", "input_schema": {"type": "object"}}
    ]


def test_any_tool_choice_maps_to_any() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request(AnyTool()))
    assert client.calls[0]["tool_choice"] == {"type": "any"}


def test_named_tool_choice_maps_to_named() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request(NamedTool(name="do")))
    assert client.calls[0]["tool_choice"] == {"type": "tool", "name": "do"}


def test_image_part_becomes_base64_image_block() -> None:
    png = b"\x89PNG\r\n\x1a\n fake-bytes"
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request(image=png))
    content = client.calls[0]["messages"][0]["content"]
    image = next(c for c in content if c["type"] == "image")
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image["source"]["data"]) == png
    assert content[0]["type"] == "image" and content[1]["type"] == "text"


def test_text_part_becomes_text_block() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    AnthropicBackend(client=client).create_message(_request())
    content = client.calls[0]["messages"][0]["content"]
    assert content == [{"type": "text", "text": "hello"}]
    assert client.calls[0]["messages"][0]["role"] == "user"


def test_response_tool_use_becomes_neutral_block() -> None:
    client = FakeAnthropic(FakeBlock("do", {"k": "v"}))
    response = AnthropicBackend(client=client).create_message(_request())
    tool_use = response.first_tool_use()
    assert isinstance(tool_use, ToolUseBlock)
    assert tool_use.name == "do" and tool_use.input == {"k": "v"}


def test_response_carries_usage_object_untouched() -> None:
    client = FakeAnthropic(FakeBlock("do", {}))
    response = AnthropicBackend(client=client).create_message(_request())
    # The raw Anthropic usage object flows through so bajutsu.usage.record reads it as before.
    assert response.usage is not None
    assert response.usage.input_tokens == 10


def test_empty_response_has_no_tool_use() -> None:
    client = FakeAnthropic()  # a message with no blocks — "model returned no tool call"
    response = AnthropicBackend(client=client).create_message(_request())
    assert response.first_tool_use() is None


class _TextThenToolMessage:
    """An Anthropic-style reply with a text block preceding the tool-use block."""

    def __init__(self) -> None:
        self.content = [FakeBlock("do", {})]
        self.content.insert(0, _RawText("thinking out loud"))
        self.usage = None
        self.stop_reason = "tool_use"


class _RawText:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _TextThenToolClient:
    def __init__(self) -> None:
        self.messages = self

    def create(self, **_: object) -> _TextThenToolMessage:
        return _TextThenToolMessage()


def test_text_blocks_are_translated_and_stop_reason_passes_through() -> None:
    response = AnthropicBackend(client=_TextThenToolClient()).create_message(_request())
    assert isinstance(response.content[0], TextBlock)
    assert response.content[0].text == "thinking out loud"
    assert isinstance(response.content[1], ToolUseBlock)
    assert response.stop_reason == "tool_use"
