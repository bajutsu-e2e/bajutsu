"""Shared test helpers: an element factory and a fake Anthropic client.

These were copy-pasted across many test modules; centralising them keeps the fakes in
one place. Plain functions/classes (not fixtures) so they can be used at module level
(e.g. building screen constants). Imported as `from conftest import ...`.
"""

from __future__ import annotations

from typing import Any

from bajutsu.drivers import base


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


class _FakeMessage:
    def __init__(self, blocks: list[FakeBlock]) -> None:
        self.content = list(blocks)


class FakeAnthropic:
    """Mimics `anthropic.Anthropic` for `client.messages.create(...)`.

    Each positional block is returned as its own single-block message on successive
    `create()` calls (the last repeats once exhausted). With no blocks, `create()` returns
    an empty message — the "model proposed no tool call" path.
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
