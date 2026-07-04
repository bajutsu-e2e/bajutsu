"""Construct the Anthropic SDK client for the configured AI provider (Tier-1 paths only).

`record`, `triage`, alert-dismissal, and `crawl` reach Claude through this one factory, so the
provider is swappable without touching the call sites. The choice comes from the resolved `ai`
config block (`defaults.ai` / `targets.<name>.ai`, BE-0047) first, falling back to the environment
so existing setups keep working unchanged (and so `bajutsu serve` can hand settings to spawned jobs
through the env):

- ``provider`` (`ai.provider` / ``BAJUTSU_AI_PROVIDER``) = ``anthropic`` (default) or ``bedrock``.
  - ``anthropic`` → ``anthropic.Anthropic()``, authenticated by the key in ``ai.keyEnv`` (default
    ``ANTHROPIC_API_KEY``); ``ai.baseUrl`` points the SDK at a self-hosted gateway / enterprise
    proxy, so a screenshot or element tree only ever reaches the user-configured endpoint.
  - ``bedrock``  → ``anthropic.AnthropicBedrock()``, authenticated by the standard AWS credential
    chain (env vars / shared profile / instance or task role) and ``AWS_REGION``.
- ``ai.model`` / ``BAJUTSU_BEDROCK_MODEL`` overrides the model id; the Bedrock path needs a
  provider-prefixed id (e.g. ``global.anthropic.claude-opus-4-6-v1``) — the bare Anthropic id is
  not a valid Bedrock model id.

Keys never live in config: ``ai.keyEnv`` names the env var, the value is read here at call time
(BE-0047). Nothing here runs in the deterministic ``run`` / CI gate (DESIGN §2 / §3.1). The SDK
itself is the optional ``ai`` extra (BE-0111) — imported lazily here, only when a model is actually
called — and the Bedrock variant (``anthropic[bedrock]``, which pulls in boto3) layers on top for
the Bedrock path. A base install without the extra raises an actionable error rather than crashing.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

# AiConfig lives in `config` (the resolved `ai` block belongs with the rest of the config, and the
# deterministic core must read it without importing this AI stack, BE-0112). Re-exported here so the
# AI paths keep importing it alongside the client factory from one module.
from bajutsu.config import AiConfig as AiConfig

PROVIDER_ENV = "BAJUTSU_AI_PROVIDER"
BEDROCK_MODEL_ENV = "BAJUTSU_BEDROCK_MODEL"
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
PROVIDERS = ("anthropic", "bedrock")


def provider(ai: AiConfig | None = None) -> str:
    """Which Anthropic-family client to build — ``anthropic`` (default) or ``bedrock``.

    Resolves *within* this adapter: `ai.provider` wins, else ``BAJUTSU_AI_PROVIDER`` (BE-0047
    config-first, env fallback), and anything outside the family normalizes to ``anthropic``. This
    is not the cross-provider authority — that is `bajutsu.ai` (BE-0104), whose registry dispatches a
    provider name to its adapter; a non-Anthropic provider is routed there and never reaches here.
    """
    raw = (ai and ai.provider) or os.environ.get(PROVIDER_ENV) or "anthropic"
    value = raw.strip().lower()
    return value if value in PROVIDERS else "anthropic"


def key_env(ai: AiConfig | None) -> str:
    """The name of the env var holding the Anthropic key — `ai.keyEnv` or the default.

    Public so the CLI can name the env var in a human-readable message without re-deriving the
    fallback (BE-0047).
    """
    return (ai and ai.key_env) or ANTHROPIC_KEY_ENV


def make_client(client: Any = None, ai: AiConfig | None = None) -> Any:
    """Return the Anthropic SDK client for the resolved provider.

    ``client`` short-circuits the factory — it is the injection seam the AI classes use in tests.
    For the Anthropic provider the key is read from the env var named by ``ai.keyEnv`` (default
    ``ANTHROPIC_API_KEY``) and ``ai.baseUrl`` (when set) points the SDK at a self-hosted gateway,
    so input only ever reaches the user-configured endpoint (BE-0047). Bedrock resolves AWS
    credentials from the environment (the chain + ``AWS_REGION``).

    Raises:
        RuntimeError: The Anthropic key env var is unset. The factory itself fails closed (BE-0047)
            rather than passing ``api_key=None`` — which the SDK would silently backfill from
            ``ANTHROPIC_API_KEY``, defeating a custom ``ai.keyEnv`` and the no-hosted-default promise.
    """
    if client is not None:
        return client
    if provider(ai) == "bedrock":
        try:
            from anthropic import AnthropicBedrock
        except ImportError as e:  # the anthropic[bedrock] extra (boto3) isn't installed
            raise RuntimeError(
                "Bedrock support needs the anthropic Bedrock extra; "
                "install it with `uv sync --extra bedrock`."
            ) from e
        return AnthropicBedrock()
    try:
        from anthropic import Anthropic
    except ImportError as e:  # the anthropic SDK (the `ai` extra, BE-0111) isn't installed
        raise RuntimeError(
            "the AI paths need the anthropic SDK; install it with `uv sync --extra ai` "
            "(or `pip install bajutsu[ai]`)."
        ) from e

    name = key_env(ai)
    api_key = os.environ.get(name)
    if not api_key:
        raise RuntimeError(f"no Anthropic API key: ${name} is unset (BE-0047 fail-closed)")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if ai and ai.base_url:
        kwargs["base_url"] = ai.base_url
    return Anthropic(**kwargs)


class CachesClient(Protocol):
    """The shape ensure_client memoizes onto — the two attrs every Claude* AI class already holds."""

    _client: Any
    _ai: AiConfig | None


def ensure_client(agent: CachesClient) -> Any:
    """Return the agent's SDK client, building and caching it on ``_client`` on first use.

    The lazy-build-then-cache wrapper the AI authoring/investigation classes share (BE-0140).
    ``make_client`` already short-circuits an injected client; the one thing this adds is
    memoizing the built client on the instance, so a class doesn't reopen the SDK client (and
    reread the key env var) on every call.
    """
    if agent._client is None:
        agent._client = make_client(ai=agent._ai)
    return agent._client


def resolve_model(default: str, ai: AiConfig | None = None) -> str:
    """The model id to use for *default*, accounting for the resolved config and provider.

    A configured ``ai.model`` wins on either provider. Otherwise Bedrock needs a provider-prefixed
    id, so ``BAJUTSU_BEDROCK_MODEL`` replaces the bare Anthropic-form *default* when the Bedrock
    provider is active; the Anthropic path uses *default* (BE-0047 config-first, env fallback).
    """
    if ai and ai.model:
        return ai.model
    if provider(ai) == "bedrock":
        return (os.environ.get(BEDROCK_MODEL_ENV) or "").strip() or default
    return default


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """What the SDK AI path is missing to authenticate with the resolved provider, or ``None`` when it can.

    The Anthropic provider needs the key named by ``ai.keyEnv`` (default ``ANTHROPIC_API_KEY``);
    Bedrock authenticates with the standard AWS credential chain (env / shared profile / instance or
    task role — resolved by the SDK, not checked here) and needs a provider-prefixed model id
    instead (``ai.model`` or ``BAJUTSU_BEDROCK_MODEL``), since the bare Anthropic id is not a valid
    Bedrock model id. Returns ``"anthropic-key"`` or ``"bedrock-model"`` so callers can phrase a
    provider-appropriate message (used by ``record`` / ``triage`` / ``--dismiss-alerts`` to fail
    closed, and by ``crawl`` / ``run`` to gate or warn, before reaching Claude).
    """
    if provider(ai) == "bedrock":
        has_model = (ai and ai.model) or os.environ.get(BEDROCK_MODEL_ENV)
        return None if has_model else "bedrock-model"
    return None if os.environ.get(key_env(ai)) else "anthropic-key"
