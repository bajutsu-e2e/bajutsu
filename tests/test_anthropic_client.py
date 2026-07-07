"""Tests for the AI provider factory (bajutsu/anthropic_client.py).

The factory chooses the Anthropic SDK client and model id from the environment so the provider is
swappable without touching the call sites. These tests pin the env-driven selection; the bedrock
branch is skipped when the anthropic[bedrock] extra (boto3) isn't installed.
"""

from __future__ import annotations

import sys

import pytest

from bajutsu import anthropic_client as ac


def test_provider_defaults_to_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    assert ac.provider() == "api-key"


def test_provider_reads_env_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "  BEDROCK ")
    assert ac.provider() == "bedrock"
    monkeypatch.setenv(ac.PROVIDER_ENV, "  ANT ")
    assert ac.provider() == "ant"
    monkeypatch.setenv(ac.PROVIDER_ENV, "nonsense")
    assert ac.provider() == "api-key"  # unknown value falls back to the default


def test_provider_accepts_the_legacy_anthropic_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    # BE-0047 shipped the direct-API provider as `anthropic`; it now canonicalizes to `api-key` so
    # an existing config / env value keeps resolving instead of falling back as "unknown".
    monkeypatch.setenv(ac.PROVIDER_ENV, "  Anthropic ")
    assert ac.provider() == "api-key"
    assert ac.normalize_provider("anthropic") == "api-key"


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
    monkeypatch.delenv(ac.MODEL_ENV, raising=False)
    assert ac.resolve_model("claude-opus-4-8") == "claude-opus-4-8"


def test_resolve_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv(ac.MODEL_ENV, "claude-sonnet-5")
    assert ac.resolve_model("claude-opus-4-8") == "claude-sonnet-5"  # env override
    # A configured ai.model still wins over the env.
    assert ac.resolve_model("claude-opus-4-8", ac.AiConfig(model="m-cfg")) == "m-cfg"


def test_resolve_effort_config_wins_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.EFFORT_ENV, raising=False)
    assert ac.resolve_effort(ac.AiConfig(effort="high")) == "high"
    assert ac.resolve_effort(ac.AiConfig(effort="TURBO")) is None  # not a recognized level
    assert ac.resolve_effort(None) is None


def test_resolve_effort_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.EFFORT_ENV, "xhigh")
    assert ac.resolve_effort(None) == "xhigh"
    assert ac.resolve_effort(ac.AiConfig(effort="low")) == "low"  # config wins over env


def test_make_client_returns_injected_client() -> None:
    sentinel = object()
    assert ac.make_client(sentinel) is sentinel


def test_make_client_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import anthropic

    assert isinstance(ac.make_client(), anthropic.Anthropic)


def test_make_client_anthropic_missing_sdk_raises_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On a base install (no `ai` extra, BE-0111) the SDK is absent; a Claude-using command must fail
    # with an install hint, not a raw ModuleNotFoundError. Setting sys.modules['anthropic'] = None
    # makes `import anthropic` raise ImportError without uninstalling the SDK from the gate's venv.
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match=r"bajutsu\[ai\]"):
        ac.make_client()


def test_make_client_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("boto3")  # provided by the anthropic[bedrock] extra
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test_secret")
    from anthropic import AnthropicBedrock

    assert isinstance(ac.make_client(), AnthropicBedrock)


# BE-0163: the `ant` provider reads a bearer token from the Anthropic CLI (probed via subprocess,
# injected here so the tests need no real `ant` install) and passes it to the SDK as auth_token —
# the `Authorization: Bearer` header — rather than an api_key.


def test_make_client_ant_uses_the_cli_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (0, "oauth-tok-test", ""))
    client = ac.make_client()
    import anthropic

    assert isinstance(client, anthropic.Anthropic)
    assert client.auth_token == "oauth-tok-test"  # bearer token, not api_key


def test_make_client_ant_fails_closed_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    with pytest.raises(RuntimeError, match=r"ant auth login"):
        ac.make_client()


def test_make_client_ant_fails_closed_when_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (1, "", "not logged in"))
    with pytest.raises(RuntimeError, match=r"no active credential"):
        ac.make_client()


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


def test_credential_gap_ant_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    assert ac.credential_gap() == ac.ANT_CLI_MISSING


def test_credential_gap_ant_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (1, "", "not logged in"))
    assert ac.credential_gap() == ac.ANT_CLI_UNAUTHENTICATED


def test_credential_gap_ant_authenticated_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "ant")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # ant ignores the API key
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (0, "oauth-tok-test", ""))
    assert ac.credential_gap() is None


# BE-0047: the resolved `ai` config drives the factory, config-first with the env fallback intact.


def test_provider_config_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "api-key")
    assert ac.provider(ac.AiConfig(provider="bedrock")) == "bedrock"


def test_provider_falls_back_to_env_when_config_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ac.PROVIDER_ENV, "bedrock")
    assert ac.provider(ac.AiConfig()) == "bedrock"


def test_resolve_model_config_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    assert ac.resolve_model("claude-opus-4-8", ac.AiConfig(model="claude-sonnet-x")) == (
        "claude-sonnet-x"
    )


def test_make_client_uses_config_base_url_and_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MY_GATEWAY_KEY", "sk-gw-test")
    ai = ac.AiConfig(base_url="https://gw.internal/v1", key_env="MY_GATEWAY_KEY")
    client = ac.make_client(ai=ai)
    import anthropic

    assert isinstance(client, anthropic.Anthropic)
    assert str(client.base_url).rstrip("/") == "https://gw.internal/v1"
    assert client.api_key == "sk-gw-test"


def test_credential_gap_uses_config_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    ai = ac.AiConfig(key_env="MY_GATEWAY_KEY")
    assert ac.credential_gap(ai) == "anthropic-key"  # the named var is unset
    monkeypatch.setenv("MY_GATEWAY_KEY", "sk-gw-test")
    assert ac.credential_gap(ai) is None


def test_credential_gap_bedrock_model_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.BEDROCK_MODEL_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ai = ac.AiConfig(provider="bedrock", model="global.anthropic.claude-opus-4-6-v1")
    assert ac.credential_gap(ai) is None


# ensure_client is the lazy-build-then-cache wrapper the six AI classes share (BE-0140): it adds
# the one thing make_client doesn't — memoizing the built client on the instance's _client attr.


class _CacheHolder:
    """A minimal stand-in for the six Claude* classes: just the two attrs ensure_client touches."""

    def __init__(self, client: object | None = None, ai: ac.AiConfig | None = None) -> None:
        self._client = client
        self._ai = ai


def test_ensure_client_returns_injected_client_without_building() -> None:
    sentinel = object()
    holder = _CacheHolder(client=sentinel)
    assert ac.ensure_client(holder) is sentinel
    assert holder._client is sentinel  # injection is left untouched, not rebuilt


def test_ensure_client_builds_once_and_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    holder = _CacheHolder()
    first = ac.ensure_client(holder)
    second = ac.ensure_client(holder)
    assert first is second  # built once, then the cached client is reused
    assert holder._client is first  # memoized on the instance


def test_make_client_fails_closed_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # The factory itself fails closed: with the key var unset it must raise, never hand the SDK
    # api_key=None (which it would backfill from ANTHROPIC_API_KEY, defeating a custom keyEnv).
    monkeypatch.delenv(ac.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-default")  # a default key that must NOT leak in
    with pytest.raises(RuntimeError, match=r"MY_GATEWAY_KEY"):
        ac.make_client(ai=ac.AiConfig(key_env="MY_GATEWAY_KEY"))
