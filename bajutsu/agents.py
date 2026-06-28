"""Authoring-agent selection.

The API agent (Anthropic API, pay-per-token) or the Claude Code agent (the `claude` CLI,
billed to a Claude subscription). Both satisfy the `Agent` protocol, so `record` is
identical apart from how the model is reached.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from bajutsu.agent import Agent

if TYPE_CHECKING:
    from bajutsu.anthropic_client import AiConfig
    from bajutsu.redaction import Redactor

AGENT_KINDS = ("api", "claude-code")

# The env var that picks the default authoring agent when no explicit `--agent` is given. `serve`
# sets it from the Settings AI-provider selector, so `record` / `crawl` jobs inherit one choice.
AGENT_ENV = "BAJUTSU_AGENT"


def resolve_kind(agent: str = "") -> str:
    """The effective agent kind: an explicit value wins, else `$BAJUTSU_AGENT`, else "api"."""
    return agent or os.environ.get(AGENT_ENV) or "api"


def make_agent(
    kind: str,
    *,
    ai: AiConfig | None = None,
    redactor: Redactor | None = None,
) -> Agent:
    """Construct the authoring agent for `kind` ("api" or "claude-code").

    The API agent honors the resolved `ai` config (provider/model/endpoint/key) and redacts its
    textual model inputs through `redactor` (BE-0047). The Claude Code agent reaches the model
    through the `claude` CLI, so the SDK provider config does not apply to it.
    """
    if kind == "api":
        from bajutsu.claude_agent import ClaudeAgent

        return ClaudeAgent(ai=ai, redactor=redactor)
    if kind == "claude-code":
        from bajutsu.claude_code_agent import ClaudeCodeAgent

        return ClaudeCodeAgent()
    raise ValueError(f"unknown agent {kind!r} (choose one of {', '.join(AGENT_KINDS)})")
