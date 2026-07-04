"""Provider registry — the extension point that maps a provider name to its adapter (BE-0104).

Generalizes the pre-BE-0104 two-value ``provider`` (`anthropic` / `bedrock`) into a registry keyed
by provider name → adapter, so adding a provider is *register an adapter*, not *edit a factory
`if`-chain*. This item ships the registry and its built-in Anthropic adapter (covering the Anthropic
API, Amazon Bedrock, and the Anthropic CLI `ant` — BE-0163); it registers no second vendor.

**The adapter contract.** A provider adapter registers an `Adapter`: a ``factory`` that builds its
`AiBackend` from the resolved `ai` config, and a ``credential_gap`` that reports what the provider
needs to authenticate (BE-0047) — a token like ``"anthropic-key"`` / ``"bedrock-model"`` or
``None`` when it can. A new adapter implements `bajutsu.ai.base.AiBackend`, then calls `register`
with these two.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from bajutsu.ai.base import AiBackend
from bajutsu.anthropic_client import PROVIDER_ENV, AiConfig

BackendFactory = Callable[[AiConfig | None], AiBackend]
CredentialGap = Callable[[AiConfig | None], str | None]


@dataclass(frozen=True)
class Adapter:
    """What a provider adapter registers: how to build its backend and its BE-0047 credential gap."""

    factory: BackendFactory
    credential_gap: CredentialGap


_ADAPTERS: dict[str, Adapter] = {}


def register(name: str, adapter: Adapter) -> None:
    """Register *adapter* under the provider *name* (idempotent — a later call overrides)."""
    _ADAPTERS[name] = adapter


def _ensure_builtins() -> None:
    """Register the built-in Anthropic adapter on first use (lazy, to keep imports acyclic).

    Keyed on the built-in names, not on the registry being empty: a third-party or test adapter
    registering first must not suppress `anthropic`/`bedrock`/`ant` (that would `KeyError` when they
    later resolve). `setdefault` also leaves an earlier explicit registration for those names intact.
    """
    if all(name in _ADAPTERS for name in ("anthropic", "bedrock", "ant")):
        return
    from bajutsu.ai import anthropic
    from bajutsu.anthropic_client import credential_gap

    adapter = Adapter(factory=anthropic.factory, credential_gap=credential_gap)
    # Anthropic API, Amazon Bedrock, and the Anthropic CLI (`ant`, BE-0163) share one adapter:
    # Bedrock is an Anthropic-SDK hosting variant (BE-0053) and `ant` an authentication variant —
    # `AnthropicBackend` is provider-agnostic once it holds a constructed SDK client.
    _ADAPTERS.setdefault("anthropic", adapter)
    _ADAPTERS.setdefault("bedrock", adapter)
    _ADAPTERS.setdefault("ant", adapter)


def known_providers() -> tuple[str, ...]:
    """The registered provider names — the open, validated set for the `ai.provider` config (BE-0104)."""
    _ensure_builtins()
    return tuple(_ADAPTERS)


def _provider_name(ai: AiConfig | None) -> str:
    """The resolved provider name, validated against the registry — this is where BE-0104 fails closed.

    The deterministic core (`config`) can't validate the name at load: it must not import this AI
    stack (BE-0112). So the registry is the single fail-closed point — the first time an AI path
    resolves the provider, an unregistered name (from config or a stray ``BAJUTSU_AI_PROVIDER``)
    raises here rather than silently falling back, so no path ever runs against a provider nobody
    registered.

    Raises:
        ValueError: the resolved name has no registered adapter.
    """
    raw = (ai and ai.provider) or os.environ.get(PROVIDER_ENV) or "anthropic"
    value = raw.strip().lower()
    _ensure_builtins()
    if value not in _ADAPTERS:
        allowed = ", ".join(repr(p) for p in _ADAPTERS)
        raise ValueError(f"unknown ai.provider {value!r}: registered providers are {allowed}")
    return value


def _adapter(ai: AiConfig | None) -> Adapter:
    return _ADAPTERS[_provider_name(ai)]


def create_backend(ai: AiConfig | None = None) -> AiBackend:
    """The `AiBackend` for the resolved provider — the single seam every AI path constructs."""
    return _adapter(ai).factory(ai)


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """What the resolved provider is missing to authenticate, or ``None`` when it can (BE-0047)."""
    return _adapter(ai).credential_gap(ai)
