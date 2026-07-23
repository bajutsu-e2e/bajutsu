"""Shared test helpers: an element factory, a fake Anthropic client, and a conformant BE-item body
builder.

These were copy-pasted across many test modules; centralising them keeps the fakes in
one place. Plain functions/classes (not fixtures) so they can be used at module level
(e.g. building screen constants). Imported as `from conftest import ...`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bajutsu.ai.base import MessageRequest, MessageResponse, ToolUseBlock
from bajutsu.analytics import ledger as usage_ledger
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from scripts.build_roadmap_index import tracking_issue_url


@pytest.fixture(autouse=True)
def _reset_usage_ledger() -> Iterator[None]:
    """Detach the process-global usage ledger and attribution around every test (BE-0196).

    A CLI-command test installs the ledger and binds attribution via module globals that outlive
    the test; resetting on both sides keeps that state from leaking between tests in a worker.
    """
    usage_ledger.reset()
    yield
    usage_ledger.reset()


class ShotDriver(FakeDriver):
    """A FakeDriver whose screenshot writes real PNG bytes, so callers that read the
    capture back (the alert guard, the crawl guide's vision path) get an image."""

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n fake")
        self.actions.append(("screenshot", path))


ROADMAP_HEADINGS_EN = ("Introduction", "Motivation", "Detailed design", "Alternatives considered")
ROADMAP_HEADINGS_JA = ("はじめに", "動機", "詳細設計", "検討した代替案")


def valid_roadmap_item_en(id_token: str = "BE-XXXX", slug: str = "a-thing") -> str:
    """A canonically-shaped English BE item body — the fixture the format checker, the mechanical
    fixer, and the stale-PR re-checker's tests all build drift on top of."""
    body = "\n\n".join(f"## {h}\n\nTBD" for h in ROADMAP_HEADINGS_EN)
    return (
        f"**English** · [日本語]({id_token}-{slug}-ja.md)\n\n"
        f"# {id_token} — A test item\n\n"
        "<!-- BE-METADATA -->\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Proposal | [{id_token}]({id_token}-{slug}.md) |\n"
        "| Author | [@0x0c](https://github.com/0x0c) |\n"
        "| Status | **Proposal** |\n"
        f"| Tracking issue | [Search]({tracking_issue_url(id_token)}) |\n"
        "| Topic | Contributor workflow |\n"
        "<!-- /BE-METADATA -->\n\n"
        f"{body}\n\n## Progress\n\nTBD\n\n## References\n\nTBD\n"
    )


def valid_roadmap_item_ja(id_token: str = "BE-XXXX", slug: str = "a-thing") -> str:
    """The Japanese mirror of :func:`valid_roadmap_item_en`."""
    body = "\n\n".join(f"## {h}\n\nTBD" for h in ROADMAP_HEADINGS_JA)
    return (
        f"[English]({id_token}-{slug}.md) · **日本語**\n\n"
        f"# {id_token} — A test item\n\n"
        "<!-- BE-METADATA -->\n"
        "| 項目 | 値 |\n"
        "|---|---|\n"
        f"| 提案 | [{id_token}]({id_token}-{slug}-ja.md) |\n"
        "| 提案者 | [@0x0c](https://github.com/0x0c) |\n"
        "| 状態 | **提案** |\n"
        f"| トラッキング Issue | [検索]({tracking_issue_url(id_token)}) |\n"
        "| トピック | コントリビューターワークフロー |\n"
        "<!-- /BE-METADATA -->\n\n"
        f"{body}\n\n## 進捗\n\nTBD\n\n## 参考\n\nTBD\n"
    )


def el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: tuple[float, float, float, float] = (0.0, 0.0, 10.0, 10.0),
) -> base.Element:
    """A query element with sensible defaults; override only what a test cares about."""
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": frame,
    }


class FakeBlock:
    """A single Anthropic `tool_use` content block."""

    def __init__(self, name: str, inp: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = inp


class FakeUsage:
    """Mimics an Anthropic response's `usage` block (the token accounting)."""

    def __init__(
        self,
        input_tokens: int = 10,
        output_tokens: int = 5,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 3,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


# Per-call token total of the default FakeUsage (10 in + 5 out + 3 cache-read), so tests that
# assert on the usage tracker can compute the expected sum from the number of create() calls.
FAKE_USAGE_PER_CALL = 18


class _FakeMessage:
    def __init__(self, blocks: list[FakeBlock]) -> None:
        self.content = list(blocks)
        self.usage = FakeUsage()


class FakeAnthropic:
    """Mimics `anthropic.Anthropic` for `client.messages.create(...)`.

    Each positional block is returned as its own single-block message on successive
    `create()` calls (the last repeats once exhausted). With no blocks, `create()` returns
    an empty message — the "model proposed no tool call" path. Used by the Anthropic-adapter
    tests, which drive the raw SDK shape; call-site tests use `FakeBackend` instead.
    """

    def __init__(self, *blocks: FakeBlock) -> None:
        self.calls: list[dict[str, Any]] = []
        self._messages = [_FakeMessage([b]) for b in blocks] or [_FakeMessage([])]
        self._i = 0
        self.messages = self  # client.messages.create(...) resolves back here

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        message = self._messages[min(self._i, len(self._messages) - 1)]
        self._i += 1
        return message


class FakeBackend:
    """A vendor-neutral `AiBackend` for call-site tests (BE-0104).

    Each scripted `FakeBlock` tool-use is returned as its own single-block `MessageResponse` on
    successive `create_message()` calls (the last repeats once exhausted). With none, returns an
    empty response — the "model proposed no tool call" path. Records each `MessageRequest` so tests
    assert on the neutral request the call site built (not any vendor's wire shape).
    """

    def __init__(self, *blocks: FakeBlock) -> None:
        self.requests: list[MessageRequest] = []
        self._responses = [
            MessageResponse(content=[ToolUseBlock(name=b.name, input=b.input)], usage=FakeUsage())
            for b in blocks
        ] or [MessageResponse(content=[], usage=FakeUsage())]
        self._i = 0

    def create_message(self, request: MessageRequest) -> MessageResponse:
        self.requests.append(request)
        response = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return response
