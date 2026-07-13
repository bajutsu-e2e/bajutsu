"""Tests for provider-agnostic AI config resolution (bajutsu/ai_config.py).

These pin the config-first / env-fallback resolution every AI backend shares — the provider name,
model id, reasoning effort, and output language. The provider *validation* (fail-closed on an
unknown name) is the registry's, exercised in `test_ai_backend.py`; this module covers only the
resolution the SDK and CLI backends read.
"""

from __future__ import annotations

import pytest

from bajutsu import ai_config as aic


def test_resolve_provider_defaults_to_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    assert aic.resolve_provider() == "api-key"


def test_resolve_provider_reads_env_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "  BEDROCK ")
    assert aic.resolve_provider() == "bedrock"
    monkeypatch.setenv(aic.PROVIDER_ENV, "  ANT ")
    assert aic.resolve_provider() == "ant"
    # An unknown name is returned verbatim: validation (fail-closed on an unregistered provider) is
    # the registry's job (BE-0104), not this low-level resolver's, so no silent clamp to the default
    # hides a typo before the registry can reject it.
    monkeypatch.setenv(aic.PROVIDER_ENV, "nonsense")
    assert aic.resolve_provider() == "nonsense"


def test_resolve_provider_accepts_the_legacy_anthropic_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0047 shipped the direct-API provider as `anthropic`; it now canonicalizes to `api-key` so
    # an existing config / env value keeps resolving instead of falling back as "unknown".
    monkeypatch.setenv(aic.PROVIDER_ENV, "  Anthropic ")
    assert aic.resolve_provider() == "api-key"
    assert aic.normalize_provider("anthropic") == "api-key"


def test_resolve_provider_config_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "api-key")
    assert aic.resolve_provider(aic.AiConfig(provider="bedrock")) == "bedrock"


def test_resolve_provider_falls_back_to_env_when_config_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "bedrock")
    assert aic.resolve_provider(aic.AiConfig()) == "bedrock"


def test_resolve_model_anthropic_ignores_bedrock_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.delenv(aic.MODEL_ENV, raising=False)  # isolate against a CI-set BAJUTSU_AI_MODEL
    monkeypatch.setenv(aic.BEDROCK_MODEL_ENV, "global.anthropic.claude-opus-4-6-v1")
    assert aic.resolve_model("claude-opus-4-8") == "claude-opus-4-8"


def test_resolve_model_bedrock_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.MODEL_ENV, raising=False)  # isolate against a CI-set BAJUTSU_AI_MODEL
    monkeypatch.setenv(aic.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv(aic.BEDROCK_MODEL_ENV, "global.anthropic.claude-opus-4-6-v1")
    assert aic.resolve_model("claude-opus-4-8") == "global.anthropic.claude-opus-4-6-v1"


def test_resolve_model_bedrock_without_override_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "bedrock")
    monkeypatch.delenv(aic.BEDROCK_MODEL_ENV, raising=False)
    monkeypatch.delenv(aic.MODEL_ENV, raising=False)
    assert aic.resolve_model("claude-opus-4-8") == "claude-opus-4-8"


def test_resolve_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.setenv(aic.MODEL_ENV, "claude-sonnet-5")
    assert aic.resolve_model("claude-opus-4-8") == "claude-sonnet-5"  # env override
    # A configured ai.model still wins over the env.
    assert aic.resolve_model("claude-opus-4-8", aic.AiConfig(model="m-cfg")) == "m-cfg"


def test_resolve_model_config_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    assert aic.resolve_model("claude-opus-4-8", aic.AiConfig(model="claude-sonnet-x")) == (
        "claude-sonnet-x"
    )


def test_resolve_effort_config_wins_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.EFFORT_ENV, raising=False)
    assert aic.resolve_effort(aic.AiConfig(effort="high")) == "high"
    assert aic.resolve_effort(aic.AiConfig(effort="TURBO")) is None  # not a recognized level
    assert aic.resolve_effort(None) is None


def test_resolve_effort_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.EFFORT_ENV, "xhigh")
    assert aic.resolve_effort(None) == "xhigh"
    assert aic.resolve_effort(aic.AiConfig(effort="low")) == "low"  # config wins over env


def test_resolve_language_config_wins_and_defaults_to_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(aic.LANGUAGE_ENV, raising=False)
    assert aic.resolve_language(aic.AiConfig(language="ja")) == "ja"
    assert aic.resolve_language(aic.AiConfig(language="EN")) == "en"  # normalized
    assert aic.resolve_language(aic.AiConfig(language="klingon")) == "auto"  # unknown → auto
    assert aic.resolve_language(aic.AiConfig(language="auto")) == "auto"
    assert aic.resolve_language(None) == "auto"


def test_resolve_language_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.LANGUAGE_ENV, "ja")
    assert aic.resolve_language(None) == "ja"
    assert (
        aic.resolve_language(aic.AiConfig(language="en")) == "en"
    )  # explicit config wins over env
    # `auto` (the default) must NOT shadow the env — else a bound config's default would disable the
    # serve dropdown's env setting. Config `auto` / unknown defers to the env.
    assert aic.resolve_language(aic.AiConfig(language="auto")) == "ja"
    assert aic.resolve_language(aic.AiConfig(language="klingon")) == "ja"


def test_language_instruction_auto_is_empty_others_name_the_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(aic.LANGUAGE_ENV, raising=False)
    assert aic.language_instruction(None) == ""  # auto appends nothing (prompt stays cacheable)
    assert aic.language_instruction(aic.AiConfig(language="auto")) == ""
    ja = aic.language_instruction(aic.AiConfig(language="ja"))
    assert "日本語" in ja and ja.startswith("\n\n")
    assert "English" in aic.language_instruction(aic.AiConfig(language="en"))
