"""Authoring-agent selection: the API agent (Anthropic API, pay-per-token) or the Claude Code
agent (the `claude` CLI, billed to a Claude subscription). Both satisfy the `Agent` protocol,
so `record` is identical apart from how the model is reached."""

from __future__ import annotations

from bajutsu.agent import Agent

AGENT_KINDS = ("api", "claude-code")


def make_agent(kind: str) -> Agent:
    """Construct the authoring agent for `kind` ("api" or "claude-code")."""
    if kind == "api":
        from bajutsu.claude_agent import ClaudeAgent

        return ClaudeAgent()
    if kind == "claude-code":
        from bajutsu.claude_code_agent import ClaudeCodeAgent

        return ClaudeCodeAgent()
    raise ValueError(f"unknown agent {kind!r} (choose one of {', '.join(AGENT_KINDS)})")
