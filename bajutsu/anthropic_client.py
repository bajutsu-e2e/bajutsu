"""Construct the Anthropic SDK client for the configured AI provider (Tier-1 paths only).

`record`, `triage`, alert-dismissal, and `crawl` reach Claude through this one factory, so the
provider is swappable without touching the call sites. The provider is chosen by the environment,
mirroring how the API key is supplied (and how `bajutsu serve` hands settings to spawned jobs):

- ``BAJUTSU_AI_PROVIDER`` = ``anthropic`` (default) or ``bedrock``.
  - ``anthropic`` → ``anthropic.Anthropic()``, authenticated by ``ANTHROPIC_API_KEY``.
  - ``bedrock``  → ``anthropic.AnthropicBedrock()``, authenticated by the standard AWS credential
    chain (env vars / shared profile / instance or task role) and ``AWS_REGION``.
- ``BAJUTSU_BEDROCK_MODEL`` overrides the model id on the Bedrock path, which needs a
  provider-prefixed id (e.g. ``global.anthropic.claude-opus-4-6-v1``) — the bare Anthropic id is
  not a valid Bedrock model id.

Nothing here runs in the deterministic ``run`` / CI gate (DESIGN §2 / §3.1). The Bedrock SDK extra
(``anthropic[bedrock]``, which pulls in boto3) is optional and only imported on the Bedrock path.
"""

from __future__ import annotations

import os
from typing import Any

PROVIDER_ENV = "BAJUTSU_AI_PROVIDER"
BEDROCK_MODEL_ENV = "BAJUTSU_BEDROCK_MODEL"
PROVIDERS = ("anthropic", "bedrock")


def provider() -> str:
    """The configured AI provider, defaulting to ``anthropic`` for any unset or unknown value."""
    value = (os.environ.get(PROVIDER_ENV) or "anthropic").strip().lower()
    return value if value in PROVIDERS else "anthropic"


def make_client(client: Any = None) -> Any:
    """Return the Anthropic SDK client for the configured provider.

    ``client`` short-circuits the factory — it is the injection seam the AI classes use in tests.
    Credentials are resolved by the SDK from the environment: ``ANTHROPIC_API_KEY`` for the
    Anthropic provider, the AWS credential chain + ``AWS_REGION`` for Bedrock.
    """
    if client is not None:
        return client
    if provider() == "bedrock":
        try:
            from anthropic import AnthropicBedrock
        except ImportError as e:  # the anthropic[bedrock] extra (boto3) isn't installed
            raise RuntimeError(
                "Bedrock support needs the anthropic Bedrock extra; "
                "install it with `uv sync --extra bedrock`."
            ) from e
        return AnthropicBedrock()
    from anthropic import Anthropic

    return Anthropic()


def resolve_model(default: str) -> str:
    """The model id to use for *default*, accounting for the provider.

    Bedrock needs a provider-prefixed model id, so ``BAJUTSU_BEDROCK_MODEL`` replaces the bare
    Anthropic-form *default* when the Bedrock provider is active. The Anthropic path uses *default*.
    """
    if provider() == "bedrock":
        return (os.environ.get(BEDROCK_MODEL_ENV) or "").strip() or default
    return default


def credential_gap() -> str | None:
    """What the SDK AI path is missing to authenticate with the configured provider, or ``None``
    when it can. The Anthropic provider needs ``ANTHROPIC_API_KEY``; Bedrock authenticates with the
    standard AWS credential chain (env / shared profile / instance or task role — resolved by the
    SDK, not checked here) and needs a provider-prefixed ``BAJUTSU_BEDROCK_MODEL`` instead, since the
    bare Anthropic id is not a valid Bedrock model id. Returns ``"anthropic-key"`` or
    ``"bedrock-model"`` so callers can phrase a provider-appropriate message (used by ``crawl`` /
    ``run`` to gate or warn before reaching Claude)."""
    if provider() == "bedrock":
        return None if os.environ.get(BEDROCK_MODEL_ENV) else "bedrock-model"
    return None if os.environ.get("ANTHROPIC_API_KEY") else "anthropic-key"
