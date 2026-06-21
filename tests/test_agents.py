"""Tests for authoring-agent selection (bajutsu.agents)."""

from __future__ import annotations

import pytest

from bajutsu.agents import AGENT_ENV, make_agent, resolve_kind


def test_resolve_kind_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit kind beats the env default.
    monkeypatch.setenv(AGENT_ENV, "claude-code")
    assert resolve_kind("api") == "api"


def test_resolve_kind_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AGENT_ENV, "claude-code")
    assert resolve_kind("") == "claude-code"


def test_resolve_kind_defaults_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AGENT_ENV, raising=False)
    assert resolve_kind("") == "api"


def test_make_agent_rejects_unknown_kind() -> None:
    # resolve_kind never validates; the construction seam does.
    with pytest.raises(ValueError, match="unknown agent"):
        make_agent("bogus")
