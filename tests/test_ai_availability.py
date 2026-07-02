"""The unified "is Claude reachable" resolver (BE-0101).

`ai_availability` generalizes `credential_gap` (the SDK path) over the agent backend, so the SDK
gaps must forward unchanged and the `claude-code` backend must be judged by binary presence — using
injected probes so the whole thing stays testable without a real key or `claude` install.
"""

from __future__ import annotations

import pytest

from bajutsu import ai_availability
from bajutsu.anthropic_client import AiConfig


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A no-AI environment, so each test controls its own signal."""
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_AI_PROVIDER",
        "BAJUTSU_BEDROCK_MODEL",
        "BAJUTSU_AGENT",
    ):
        monkeypatch.delenv(var, raising=False)


# ---- SDK (api) backend: the existing credential_gap answer, forwarded unchanged ----


def test_api_backend_reports_missing_anthropic_key() -> None:
    assert ai_availability.availability(agent_kind="api") == "anthropic-key"


def test_api_backend_reachable_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ai_availability.availability(agent_kind="api") is None


def test_api_backend_honors_custom_key_env() -> None:
    ai = AiConfig(key_env="MY_KEY")
    assert ai_availability.availability(agent_kind="api", ai=ai) == "anthropic-key"


def test_bedrock_backend_needs_a_model() -> None:
    ai = AiConfig(provider="bedrock")
    assert ai_availability.availability(agent_kind="api", ai=ai) == "bedrock-model"
    ai_ok = AiConfig(provider="bedrock", model="global.anthropic.claude")
    assert ai_availability.availability(agent_kind="api", ai=ai_ok) is None


# ---- claude-code backend: binary presence only (login is not inferred; see the module docstring) ----


def test_claude_code_missing_binary() -> None:
    gap = ai_availability.availability(agent_kind="claude-code", which=lambda _exe: None)
    assert gap == ai_availability.CLAUDE_CODE_MISSING


def test_claude_code_reachable_when_binary_present() -> None:
    gap = ai_availability.availability(
        agent_kind="claude-code", which=lambda _exe: "/usr/local/bin/claude"
    )
    assert gap is None


def test_claude_code_reachability_ignores_credentials() -> None:
    # No key, no Bedrock model — still reachable, because the CLI carries its own subscription login.
    assert (
        ai_availability.availability(agent_kind="claude-code", which=lambda _exe: "/bin/claude")
        is None
    )


# ---- from_env: the serve process resolves the backend from BAJUTSU_AGENT ----


def test_from_env_uses_claude_code_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    gap = ai_availability.from_env({"BAJUTSU_AGENT": "claude-code"}, which=lambda _exe: None)
    assert gap == ai_availability.CLAUDE_CODE_MISSING


def test_from_env_defaults_to_api_backend() -> None:
    assert ai_availability.from_env({}) == "anthropic-key"


# ---- message: an actionable one-liner per gap ----


@pytest.mark.parametrize(
    "gap",
    ["anthropic-key", "bedrock-model", ai_availability.CLAUDE_CODE_MISSING],
)
def test_message_is_specific_and_actionable(gap: str) -> None:
    msg = ai_availability.message(gap)
    assert msg and not msg.endswith(gap)  # a phrased message, not the raw token
