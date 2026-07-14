"""Provider-agnostic AI config resolution (Tier-1 paths only).

Every AI backend — the Anthropic SDK variants *and* `claude-code` — resolves the same handful of
knobs from the effective `ai` config block (`defaults.ai` / `targets.<name>.ai`, BE-0047) first,
falling back to the environment so existing setups keep working (and so `bajutsu serve` can hand
settings to spawned jobs through the env): which provider to reach, the model id, the reasoning
effort, and the output language. None of it is Anthropic-SDK-specific, so it lives here rather than
behind a vendor name — `bajutsu.anthropic_client` keeps only the code that actually constructs the
Anthropic SDK client (BE-0246).

Keys never live in config: a provider names the env var, the value is read at call time by whoever
builds the client (BE-0047). Nothing here runs in the deterministic `run` / CI gate (DESIGN §2 /
§3.1); it is imported only on the Tier-1 authoring / investigation paths.
"""

from __future__ import annotations

import os

# AiConfig lives in `config` (the resolved `ai` block belongs with the rest of the config, and the
# deterministic core must read it without importing this AI stack, BE-0112). Re-exported here so the
# AI paths import the config type alongside the resolvers that read it from one module.
from bajutsu.config import AiConfig as AiConfig

PROVIDER_ENV = "BAJUTSU_AI_PROVIDER"
MODEL_ENV = "BAJUTSU_AI_MODEL"  # provider-agnostic model override (config `ai.model` wins over it)
EFFORT_ENV = "BAJUTSU_AI_EFFORT"  # reasoning-effort override (config `ai.effort` wins over it)
# AI output language override, BE-0188 (config `ai.language` wins over it).
LANGUAGE_ENV = "BAJUTSU_AI_LANGUAGE"
BEDROCK_MODEL_ENV = "BAJUTSU_BEDROCK_MODEL"
# The env vars serve manages when it materializes an AI provider selection into a spawned job's
# environment (BE-0229). A per-job overlay that names a provider clears these from the inherited
# env before applying its own values, so one org's selection can't leak into another org's jobs
# through a stale process-env var. AWS_REGION is deliberately excluded: it is a general AWS setting
# a deployment may set independently of the provider selection, so the overlay only ever adds it
# (for bedrock, when a region is chosen), never clears it.
PROVIDER_MANAGED_ENV = frozenset(
    {PROVIDER_ENV, MODEL_ENV, EFFORT_ENV, LANGUAGE_ENV, BEDROCK_MODEL_ENV}
)
# The reasoning-effort levels the `claude` CLI accepts (`--effort`); other levels are ignored.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# The AI output languages `ai.language` / `--language` accept (BE-0188). `auto` keeps today's
# behavior (record follows the goal, crawl stays English); the human-readable name each maps to in
# the prompt instruction lives in `_LANGUAGE_NAMES` below.
LANGUAGES = ("auto", "ja", "en")
DEFAULT_LANGUAGE = "auto"
# Provider names state the authentication method — all three are Anthropic-SDK variants.
PROVIDERS = ("api-key", "bedrock", "ant")
DEFAULT_PROVIDER = "api-key"

# Backward-compatible provider aliases. BE-0047 / BE-0053 shipped the direct-API provider as
# ``anthropic``; it was renamed to ``api-key`` (the name now states the *auth method*, since Bedrock
# and ``ant`` are Anthropic too), so an existing config or ``$BAJUTSU_AI_PROVIDER=anthropic`` keeps
# resolving instead of failing closed on an "unknown provider".
_PROVIDER_ALIASES = {"anthropic": "api-key"}


def normalize_provider(raw: str) -> str:
    """Canonicalize a provider name — trim, lowercase, and resolve a backward-compatible alias.

    Shared by `resolve_provider` and the cross-provider registry (BE-0104), so both accept the same
    spellings and the legacy ``anthropic`` name maps to ``api-key`` in one place.
    """
    value = raw.strip().lower()
    return _PROVIDER_ALIASES.get(value, value)


def resolve_provider(ai: AiConfig | None = None) -> str:
    """The configured provider name — `ai.provider`, else ``BAJUTSU_AI_PROVIDER``, else the default.

    The one place the config-first / env-fallback provider name is read and normalized (BE-0047).
    It does **not** validate the name against the registered adapters: that fail-closed check is the
    registry's (`bajutsu.ai.registry`, BE-0104), which builds on this. So an unknown name is returned
    verbatim here and rejected there — the Anthropic-family call sites that use this directly
    (`make_client`, `resolve_model`) only ever run after the registry has already accepted the name.
    """
    raw = (ai and ai.provider) or os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER
    return normalize_provider(raw)


def resolve_model(default: str, ai: AiConfig | None = None) -> str:
    """The model id to use for *default*, accounting for the resolved config and provider.

    A configured ``ai.model`` wins on any provider. Otherwise Bedrock needs a provider-prefixed
    id, so ``BAJUTSU_BEDROCK_MODEL`` replaces the bare Anthropic-form *default* when the Bedrock
    provider is active; the Anthropic and ``ant`` paths use *default* — the bare Anthropic id (their
    model catalog matches the SDK's) (BE-0047 config-first, env fallback).
    """
    if ai and ai.model:
        return ai.model
    env_model = (os.environ.get(MODEL_ENV) or "").strip()
    if env_model:
        return env_model
    if resolve_provider(ai) == "bedrock":
        return (os.environ.get(BEDROCK_MODEL_ENV) or "").strip() or default
    return default


def resolve_effort(ai: AiConfig | None = None) -> str | None:
    """The reasoning-effort level for this call, or ``None`` when unset / unrecognized.

    Config ``ai.effort`` wins, else ``BAJUTSU_AI_EFFORT`` (config-first, env fallback — like the
    provider and model). Only the levels the `claude` CLI accepts (`EFFORT_LEVELS`) pass through; any
    other value resolves to ``None`` so a typo silently falls back to the model's default rather than
    failing a run. Providers that have no effort knob (the Anthropic SDK) ignore the result.
    """
    raw = (ai.effort if ai else None) or os.environ.get(EFFORT_ENV) or ""
    value = raw.strip().lower()
    return value if value in EFFORT_LEVELS else None


# The human-readable name each language enum maps to in the prompt instruction. `auto` has no name
# because it appends nothing (today's emergent behavior); the trailing native form disambiguates for
# the model (e.g. that "ja" means 日本語, not romaji).
_LANGUAGE_NAMES = {"ja": "Japanese (日本語)", "en": "English"}


def resolve_language(ai: AiConfig | None = None) -> str:
    """The AI output language for this call — one of ``LANGUAGES`` (BE-0188).

    An explicit ``ja`` / ``en`` in ``ai.language`` wins (config-first, like the provider, model, and
    effort). Otherwise — an ``auto``, absent, or unrecognized config value — the language falls back
    to ``BAJUTSU_AI_LANGUAGE``, else ``auto``. Unlike ``effort``, ``auto`` is both a real value *and*
    the default, so it must **not** shadow the env var: a bound config carrying the default ``auto``
    would otherwise silently disable the language the ``serve`` dropdown sets through the env. A typo
    keeps today's behavior rather than failing an AI path. This governs only the language the model
    writes its own generated prose in; it never enters the deterministic run/CI verdict.
    """
    configured = ((ai.language if ai else None) or "").strip().lower()
    if configured in _LANGUAGE_NAMES:  # an explicit ja/en in config wins over the env
        return configured
    env = os.environ.get(LANGUAGE_ENV, "").strip().lower()
    return env if env in _LANGUAGE_NAMES else DEFAULT_LANGUAGE


def language_instruction(ai: AiConfig | None = None) -> str:
    """A system-prompt suffix constraining the model's free-text language, or ``""`` for ``auto``.

    Folded onto an AI path's static system prompt (`record` authoring/enrichment, `crawl`
    guide/tabs) so the model's generated prose — reasoning, intent, and any provenance — comes out in
    the chosen language. ``auto`` (the default) appends nothing, so the prompt is byte-identical to
    today and stays prompt-cacheable. Applies only to authoring/investigation prose, never the
    deterministic verdict.
    """
    name = _LANGUAGE_NAMES.get(resolve_language(ai))
    if name is None:  # auto → append nothing
        return ""
    return (
        f"\n\nWrite all of your free-text output — your reasoning, intent, and any "
        f"provenance or descriptions — in {name}."
    )
