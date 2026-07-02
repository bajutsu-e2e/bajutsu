"""Tests for the bajutsu serve AI-provider endpoint (/api/provider), real ThreadingHTTPServer.

The endpoint mirrors /api/apikey: it writes the provider selection into the serve process's
environment (in memory, never to disk) so spawned record/crawl jobs inherit it via os.environ.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import ai_availability
from bajutsu import anthropic_client as ac
from bajutsu import serve as srv
from bajutsu.agents import AGENT_ENV

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"


def _clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a known provider state; monkeypatch restores the originals at teardown even
    though the handler writes os.environ directly."""
    for var in (
        ac.PROVIDER_ENV,
        ac.BEDROCK_MODEL_ENV,
        ac.ANTHROPIC_KEY_ENV,
        "AWS_REGION",
        AGENT_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


def test_http_provider_select_bedrock_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip the AI provider through the WebUI: default anthropic → bedrock (region + model
    land in the process env) → back to anthropic. Nothing is written to disk."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        # No key set, so the record/crawl tabs would read disabled (BE-0101): the payload carries the
        # reachability the front end gates on, with an actionable hint.
        assert _get_json(port, "/api/provider") == {
            "provider": "anthropic",
            "region": "",
            "model": "",
            "claudeAvailable": False,
            "claudeGap": "anthropic-key",
            "claudeHint": ai_availability.message("anthropic-key"),
        }
        code, body = _post(
            port,
            "/api/provider",
            {"provider": "bedrock", "region": "us-east-1", "model": _BEDROCK_MODEL},
        )
        assert code == 200 and body["provider"] == "bedrock"
        assert os.environ[ac.PROVIDER_ENV] == "bedrock"
        assert os.environ[AGENT_ENV] == "api"  # an SDK provider implies the API authoring agent
        assert os.environ["AWS_REGION"] == "us-east-1"
        assert os.environ[ac.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        # Bedrock with a provider-prefixed model id is reachable (AWS creds authenticate it), so the
        # gate reports available and the front end re-enables the Claude tabs.
        assert _get_json(port, "/api/provider") == {
            "provider": "bedrock",
            "region": "us-east-1",
            "model": _BEDROCK_MODEL,
            "claudeAvailable": True,
            "claudeGap": None,
            "claudeHint": "",
        }
        # Switch back to the Anthropic API.
        code, body = _post(port, "/api/provider", {"provider": "anthropic"})
        assert code == 200 and body["provider"] == "anthropic"
        assert os.environ[ac.PROVIDER_ENV] == "anthropic"
        assert _get_json(port, "/api/provider")["provider"] == "anthropic"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_bedrock_requires_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bedrock needs a provider-prefixed model id; without one the request is rejected and no
    provider env is set."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "bedrock", "region": "us-east-1"})
        assert code == 400 and "model" in body["error"]
        assert ac.BEDROCK_MODEL_ENV not in os.environ
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_select_claude_code_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code is reported as a third "provider" but is really the authoring agent: selecting
    it sets BAJUTSU_AGENT=claude-code (and leaves the SDK provider at anthropic for the alert
    guard); switching to an SDK provider clears it back to api."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "claude-code"})
        assert code == 200 and body["provider"] == "claude-code"
        assert os.environ[AGENT_ENV] == "claude-code"
        assert os.environ[ac.PROVIDER_ENV] == "anthropic"  # SDK paths (alert guard) still defined
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        assert _get_json(port, "/api/provider")["provider"] == "claude-code"
        # Switching to an SDK provider clears the Claude Code selection.
        code, body = _post(port, "/api/provider", {"provider": "anthropic"})
        assert code == 200 and body["provider"] == "anthropic"
        assert os.environ[AGENT_ENV] == "api"
        assert _get_json(port, "/api/provider")["provider"] == "anthropic"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_rejects_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "vertex"})
        assert code == 400 and "unknown provider" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
