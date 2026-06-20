"""Tests for the AI provider factory (bajutsu/anthropic_client.py).

The factory chooses the Anthropic SDK client and model id from the environment so the provider is
swappable without touching the call sites. These tests pin the env-driven selection; the bedrock
branch is skipped when the anthropic[bedrock] extra (boto3) isn't installed.
"""

from __future__ import annotations

import pytest

from bajutsu import anthropic_client as ac


def test_provider_defaults_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    assert ac.provider() == "anthropic"


def test_provider_reads_env_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "  BEDROCK ")
    assert ac.provider() == "bedrock"
    monkeypatch.setenv(ac.PROVIDER_ENV, "nonsense")
    assert ac.provider() == "anthropic"  # unknown value falls back to the default


def test_resolve_model_anthropic_ignores_bedrock_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv(ac.BEDROCK_MODEL_ENV, "global.anthropic.claude-opus-4-6-v1")
    assert ac.resolve_model("claude-opus-4-8") == "claude-opus-4-8"


def test_resolve_model_bedrock_uses_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv(ac.BEDROCK_MODEL_ENV, "global.anthropic.claude-opus-4-6-v1")
    assert ac.resolve_model("claude-opus-4-8") == "global.anthropic.claude-opus-4-6-v1"


def test_resolve_model_bedrock_without_override_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.delenv(ac.BEDROCK_MODEL_ENV, raising=False)
    assert ac.resolve_model("claude-opus-4-8") == "claude-opus-4-8"


def test_make_client_returns_injected_client() -> None:
    sentinel = object()
    assert ac.make_client(sentinel) is sentinel


def test_make_client_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import anthropic

    assert isinstance(ac.make_client(), anthropic.Anthropic)


def test_make_client_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("boto3")  # provided by the anthropic[bedrock] extra
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test_secret")
    from anthropic import AnthropicBedrock

    assert isinstance(ac.make_client(), AnthropicBedrock)


# credential_gap reports the provider-specific credential the SDK AI path is missing, so crawl/run
# can gate or warn appropriately. The Bedrock cases pin BE-0053: AWS auth, not ANTHROPIC_API_KEY.


def test_credential_gap_anthropic_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ac.credential_gap() == "anthropic-key"


def test_credential_gap_anthropic_with_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert ac.credential_gap() is None


def test_credential_gap_bedrock_with_model_needs_no_anthropic_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On Bedrock a missing ANTHROPIC_API_KEY is fine — AWS credentials authenticate, and only a
    # provider-prefixed model id is required.
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv(ac.BEDROCK_MODEL_ENV, "global.anthropic.claude-opus-4-6-v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ac.credential_gap() is None


def test_credential_gap_bedrock_needs_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.delenv(ac.BEDROCK_MODEL_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ac.credential_gap() == "bedrock-model"
