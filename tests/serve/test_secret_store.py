"""Unit tests for the SecretStore seam (BE-0136 write-once secrets).

The local EnvSecretStore is the default: set/describe over os.environ, never a plaintext get an
HTTP handler can reach. The seam maps a logical secret name (e.g. "aiApiKey") to an env var so a
spawned record/run job inherits it, exactly as today's set_api_key did before the seam.
"""

from __future__ import annotations

import pytest

from bajutsu.serve.secrets import EnvSecretStore


def test_env_store_set_then_describe_returns_masked_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    store = EnvSecretStore(lambda name: "ANTHROPIC_API_KEY")

    assert store.describe("aiApiKey") is None  # unset

    masked = store.set("aiApiKey", "sk-ant-secret-12345")
    # describe / set only ever hand back a masked preview — never the plaintext.
    assert masked == "sk-a…2345"
    assert "secret" not in masked
    assert store.describe("aiApiKey") == "sk-a…2345"
    # The value lands in the env so a spawned job inherits it (today's behavior, behind the seam).
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-12345"


def test_env_store_empty_value_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-existing-99999")
    store = EnvSecretStore(lambda name: "ANTHROPIC_API_KEY")

    assert store.set("aiApiKey", "") is None
    assert store.describe("aiApiKey") is None
    import os

    assert "ANTHROPIC_API_KEY" not in os.environ


def test_env_store_has_no_plaintext_get() -> None:
    # The interface an HTTP handler reaches must never expose the plaintext (BE-0136).
    assert not hasattr(EnvSecretStore, "get")


def test_env_store_resolver_maps_name_to_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # The name->env-var resolver honors a config's ai.keyEnv (BE-0097): a set writes under the
    # resolved var, not a hardcoded one.
    monkeypatch.delenv("MY_CUSTOM_KEY", raising=False)
    store = EnvSecretStore(lambda name: "MY_CUSTOM_KEY")

    store.set("aiApiKey", "sk-custom-12345")
    import os

    assert os.environ["MY_CUSTOM_KEY"] == "sk-custom-12345"
    assert store.describe("aiApiKey") == "sk-c…2345"
