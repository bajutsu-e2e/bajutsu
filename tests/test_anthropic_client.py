"""Tests for the Anthropic SDK client factory (bajutsu/anthropic_client.py).

The factory constructs the Anthropic SDK client for the resolved provider (api-key / bedrock / ant)
so the provider is swappable without touching the call sites; the `ant` path reads its bearer token
from the Anthropic CLI. These pin the client construction and the `ant` credential probe; the
provider-agnostic model / effort / language resolution lives in `test_ai_config.py`, and the
cross-provider credential dispatch in `test_ai_backend.py`. The bedrock branch is skipped when the
anthropic[bedrock] extra (boto3) isn't installed.
"""

from __future__ import annotations

import sys

import pytest

from bajutsu import ai_config as aic
from bajutsu import anthropic_client as ac


def test_make_client_returns_injected_client() -> None:
    sentinel = object()
    assert ac.make_client(sentinel) is sentinel


def test_make_client_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import anthropic

    assert isinstance(ac.make_client(), anthropic.Anthropic)


def test_make_client_anthropic_missing_sdk_raises_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On a base install (no `ai` extra, BE-0111) the SDK is absent; a Claude-using command must fail
    # with an install hint, not a raw ModuleNotFoundError. Setting sys.modules['anthropic'] = None
    # makes `import anthropic` raise ImportError without uninstalling the SDK from the gate's venv.
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match=r"bajutsu\[ai\]"):
        ac.make_client()


def test_make_client_bedrock(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("boto3")  # provided by the anthropic[bedrock] extra
    monkeypatch.setenv(aic.PROVIDER_ENV, "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test_secret")
    from anthropic import AnthropicBedrock

    assert isinstance(ac.make_client(), AnthropicBedrock)


# BE-0163: the `ant` provider reads a bearer token from the Anthropic CLI (probed via subprocess,
# injected here so the tests need no real `ant` install) and passes it to the SDK as auth_token —
# the `Authorization: Bearer` header — rather than an api_key.


def test_make_client_ant_uses_the_cli_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "ant")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (0, "oauth-tok-test", ""))
    client = ac.make_client()
    import anthropic

    assert isinstance(client, anthropic.Anthropic)
    assert client.auth_token == "oauth-tok-test"  # bearer token, not api_key


def test_make_client_ant_fails_closed_when_binary_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    with pytest.raises(RuntimeError, match=r"ant auth login"):
        ac.make_client()


def test_make_client_ant_fails_closed_when_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(aic.PROVIDER_ENV, "ant")
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (1, "", "not logged in"))
    with pytest.raises(RuntimeError, match=r"no active credential"):
        ac.make_client()


def test_make_client_uses_config_base_url_and_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MY_GATEWAY_KEY", "sk-gw-test")
    ai = aic.AiConfig(base_url="https://gw.internal/v1", key_env="MY_GATEWAY_KEY")
    client = ac.make_client(ai=ai)
    import anthropic

    assert isinstance(client, anthropic.Anthropic)
    assert str(client.base_url).rstrip("/") == "https://gw.internal/v1"
    assert client.api_key == "sk-gw-test"


def test_make_client_fails_closed_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # The factory itself fails closed: with the key var unset it must raise, never hand the SDK
    # api_key=None (which it would backfill from ANTHROPIC_API_KEY, defeating a custom keyEnv).
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MY_GATEWAY_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-default")  # a default key that must NOT leak in
    with pytest.raises(RuntimeError, match=r"MY_GATEWAY_KEY"):
        ac.make_client(ai=aic.AiConfig(key_env="MY_GATEWAY_KEY"))


# The `ant` half of the Anthropic adapter's credential check (BE-0163), kept beside the token IO it
# probes; the cross-provider dispatch that reaches it lives in `bajutsu.ai` (test_ai_backend.py).


def test_ant_credential_gap_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    assert ac.ant_credential_gap() == ac.ANT_CLI_MISSING


def test_ant_credential_gap_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (1, "", "not logged in"))
    assert ac.ant_credential_gap() == ac.ANT_CLI_UNAUTHENTICATED


def test_ant_credential_gap_authenticated_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: "/usr/local/bin/ant")
    monkeypatch.setattr(ac, "_ant_token_result", lambda: (0, "oauth-tok-test", ""))
    assert ac.ant_credential_gap() is None


# ensure_client is the lazy-build-then-cache wrapper the AI classes share (BE-0140): it adds the one
# thing make_client doesn't — memoizing the built client on the instance's _client attr. (A separate
# proposal, BE-0249, removes this now-dead wrapper; until then it stays covered.)


class _CacheHolder:
    """A minimal stand-in for the Claude* classes: just the two attrs ensure_client touches."""

    def __init__(self, client: object | None = None, ai: aic.AiConfig | None = None) -> None:
        self._client = client
        self._ai = ai


def test_ensure_client_returns_injected_client_without_building() -> None:
    sentinel = object()
    holder = _CacheHolder(client=sentinel)
    assert ac.ensure_client(holder) is sentinel
    assert holder._client is sentinel  # injection is left untouched, not rebuilt


def test_ensure_client_builds_once_and_reuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(aic.PROVIDER_ENV, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    holder = _CacheHolder()
    first = ac.ensure_client(holder)
    second = ac.ensure_client(holder)
    assert first is second  # built once, then the cached client is reused
    assert holder._client is first  # memoized on the instance
