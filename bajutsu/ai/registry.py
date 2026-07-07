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
from bajutsu.anthropic_client import (
    DEFAULT_PROVIDER,
    PROVIDER_ENV,
    AiConfig,
    normalize_provider,
    resolve_model,
)

BackendFactory = Callable[[AiConfig | None], AiBackend]
CredentialGap = Callable[[AiConfig | None], str | None]
# (ai, resolved provider name, the command's primary-agent default model) → startup disclosure lines.
Announce = Callable[[AiConfig | None, str, str], list[str]]


def _default_announce(ai: AiConfig | None, provider: str, default_model: str) -> list[str]:
    """The generic startup line — provider and resolved model, nothing provider-specific.

    Names only what every provider has (a provider and a model). A provider with its own knobs —
    reasoning effort, a distinct auth mode — supplies its own `announce` to disclose them; a new
    adapter that doesn't gets this, so it never starts silently.
    """
    return [f"🤖 AI: {provider} · model {resolve_model(default_model, ai)}"]


@dataclass(frozen=True)
class Adapter:
    """What a provider adapter registers: how to build its backend and its BE-0047 credential gap.

    ``announce`` produces the provider's startup disclosure lines (BE-0176 follow-up); it defaults to
    the generic provider+model line, so only a provider with extra knobs to surface overrides it.
    """

    factory: BackendFactory
    credential_gap: CredentialGap
    announce: Announce = _default_announce


_ADAPTERS: dict[str, Adapter] = {}


def register(name: str, adapter: Adapter) -> None:
    """Register *adapter* under the provider *name* (idempotent — a later call overrides)."""
    _ADAPTERS[name] = adapter


def _ensure_builtins() -> None:
    """Register the built-in Anthropic adapter on first use (lazy, to keep imports acyclic).

    Keyed on the built-in names, not on the registry being empty: a third-party or test adapter
    registering first must not suppress `api-key`/`bedrock`/`ant` (that would `KeyError` when they
    later resolve). `setdefault` also leaves an earlier explicit registration for those names intact.
    """
    if all(name in _ADAPTERS for name in ("api-key", "bedrock", "ant", "claude-code")):
        return
    from bajutsu.ai import anthropic, claude_code
    from bajutsu.anthropic_client import credential_gap

    adapter = Adapter(factory=anthropic.factory, credential_gap=credential_gap)
    # The direct Anthropic API (`api-key`), Amazon Bedrock, and the Anthropic CLI (`ant`, BE-0163)
    # share one adapter: Bedrock is an Anthropic-SDK hosting variant (BE-0053) and `ant` an
    # authentication variant — `AnthropicBackend` is provider-agnostic once it holds a constructed
    # SDK client. The legacy `anthropic` name resolves via `normalize_provider`, so it needs no slot.
    _ADAPTERS.setdefault("api-key", adapter)
    _ADAPTERS.setdefault("bedrock", adapter)
    _ADAPTERS.setdefault("ant", adapter)
    # `claude-code` (BE-0176) is genuinely separate: it shells out to the `claude` CLI rather than
    # the Anthropic SDK, so it registers its own factory and credential gap, not another alias.
    _ADAPTERS.setdefault(
        "claude-code",
        Adapter(
            factory=claude_code.factory,
            credential_gap=claude_code.credential_gap,
            announce=claude_code.announce,
        ),
    )


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
    raw = (ai and ai.provider) or os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER
    value = normalize_provider(raw)
    _ensure_builtins()
    if value not in _ADAPTERS:
        allowed = ", ".join(repr(p) for p in _ADAPTERS)
        raise ValueError(f"unknown ai.provider {value!r}: registered providers are {allowed}")
    return value


def resolved_provider(ai: AiConfig | None = None) -> str:
    """The registry-resolved provider name, for surfaces that report the current selection.

    The soft counterpart to `_provider_name`: an unregistered name falls back to the default rather
    than raising, since a status read (serve / doctor) must not blow up. The fail-closed raise stays
    on the AI paths, which resolve through `create_backend` / `credential_gap`.
    """
    try:
        return _provider_name(ai)
    except ValueError:
        return DEFAULT_PROVIDER


def _adapter(ai: AiConfig | None) -> Adapter:
    return _ADAPTERS[_provider_name(ai)]


def create_backend(ai: AiConfig | None = None) -> AiBackend:
    """The `AiBackend` for the resolved provider — the single seam every AI path constructs."""
    return _adapter(ai).factory(ai)


def credential_gap(ai: AiConfig | None = None) -> str | None:
    """What the resolved provider is missing to authenticate, or ``None`` when it can (BE-0047)."""
    return _adapter(ai).credential_gap(ai)


def announcement(default_model: str, ai: AiConfig | None = None) -> list[str]:
    """The resolved provider's own startup disclosure lines (BE-0176 follow-up).

    Each provider decides what it discloses — the Anthropic SDK names only provider and model, while
    `claude-code` adds the reasoning effort it honors and its forced-subscription auth mode. Resolves
    the provider softly (like `resolved_provider`) so a display path never raises on an unknown name.

    Args:
        default_model: The calling command's primary-agent model id, resolved per provider.
        ai: The effective AI config; ``None`` resolves from the environment defaults.
    """
    provider = resolved_provider(ai)
    return _ADAPTERS[provider].announce(ai, provider, default_model)
