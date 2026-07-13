"""Tests for authoring-agent construction (bajutsu.agent_factory).

There is one authoring agent — the SDK-based `ClaudeAgent` — whose provider (Anthropic API /
Bedrock / the Anthropic CLI `ant`) is a property of the resolved `ai` config (BE-0104 / BE-0163),
not a separate agent kind. `make_agent` just builds it.
"""

from __future__ import annotations

from bajutsu.agent_factory import make_agent, make_enrichment_agent
from bajutsu.ai_config import AiConfig


def test_make_agent_builds_the_sdk_authoring_agent() -> None:
    from bajutsu.claude_agent import ClaudeAgent

    agent = make_agent(ai=AiConfig(provider="ant"))
    assert isinstance(agent, ClaudeAgent)


def test_make_enrichment_agent_builds_the_enrichment_agent() -> None:
    from bajutsu.claude_enrich_agent import ClaudeEnrichmentAgent

    assert isinstance(make_enrichment_agent(), ClaudeEnrichmentAgent)
