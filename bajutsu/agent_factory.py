"""Authoring-agent construction.

`record` and `crawl` reach the model through the SDK-based `AiBackend` seam (BE-0104), so the
authoring agent is provider-agnostic: the resolved `ai` config (BE-0047) picks Anthropic API,
Amazon Bedrock, or the Anthropic CLI (`ant`, BE-0163). There is one authoring agent — the
`ClaudeAgent` — behind the `Agent` protocol; subscription/SSO billing is a property of the `ant`
provider, not a separate agent kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bajutsu.agent_protocols import Agent, EnrichmentAgent

if TYPE_CHECKING:
    from bajutsu.ai_config import AiConfig
    from bajutsu.redaction import Redactor


def make_agent(
    *,
    ai: AiConfig | None = None,
    redactor: Redactor | None = None,
) -> Agent:
    """Construct the authoring agent (BE-0104).

    Honors the resolved `ai` config (provider/model/endpoint/credential) and redacts its textual
    model inputs through `redactor` (BE-0047).
    """
    from bajutsu.claude_agent import ClaudeAgent

    return ClaudeAgent(ai=ai, redactor=redactor)


def make_enrichment_agent(
    *,
    ai: AiConfig | None = None,
    redactor: Redactor | None = None,
) -> EnrichmentAgent:
    """Construct the enrichment agent (BE-0014)."""
    from bajutsu.claude_enrich_agent import ClaudeEnrichmentAgent

    return ClaudeEnrichmentAgent(ai=ai, redactor=redactor)
