"""The "is Claude reachable" resolver (BE-0101).

Every AI entry point resolves through one `ai.provider` (BE-0163), so `ai_availability` is a thin
wrapper over `credential_gap`: the SDK gaps forward unchanged, and `message` phrases an actionable
one-liner per gap token.
"""

from __future__ import annotations

import pytest

from bajutsu import ai_availability
from bajutsu.anthropic_client import ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED, AiConfig


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A no-AI environment, so each test controls its own signal."""
    for var in (
        "ANTHROPIC_API_KEY",
        "BAJUTSU_AI_PROVIDER",
        "BAJUTSU_BEDROCK_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


# ---- availability forwards the resolved provider's credential_gap unchanged ----


def test_reports_missing_anthropic_key() -> None:
    assert ai_availability.availability() == "anthropic-key"


def test_reachable_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ai_availability.availability() is None


def test_honors_custom_key_env() -> None:
    ai = AiConfig(key_env="MY_KEY")
    assert ai_availability.availability(ai=ai) == "anthropic-key"


def test_bedrock_needs_a_model() -> None:
    ai = AiConfig(provider="bedrock")
    assert ai_availability.availability(ai=ai) == "bedrock-model"
    ai_ok = AiConfig(provider="bedrock", model="global.anthropic.claude")
    assert ai_availability.availability(ai=ai_ok) is None


def test_ant_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_availability, "credential_gap", lambda _ai: ANT_CLI_MISSING)
    assert ai_availability.availability(ai=AiConfig(provider="ant")) == ANT_CLI_MISSING


# ---- message: an actionable one-liner per gap ----


@pytest.mark.parametrize(
    "gap",
    ["anthropic-key", "bedrock-model", ANT_CLI_MISSING, ANT_CLI_UNAUTHENTICATED],
)
def test_message_is_specific_and_actionable(gap: str) -> None:
    msg = ai_availability.message(gap)
    assert msg and not msg.endswith(gap)  # a phrased message, not the raw token
