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

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"


def _clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from a known provider state; monkeypatch restores the originals at teardown even
    though the handler writes os.environ directly."""
    for var in (
        ac.PROVIDER_ENV,
        ac.BEDROCK_MODEL_ENV,
        ac.ANTHROPIC_KEY_ENV,
        ac.EFFORT_ENV,
        ac.LANGUAGE_ENV,
        "AWS_REGION",
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
            "provider": "api-key",
            "region": "",
            "model": "",
            "aiModel": "",
            "effort": "",
            "language": "",
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
        assert os.environ["AWS_REGION"] == "us-east-1"
        assert os.environ[ac.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        # Bedrock with a provider-prefixed model id is reachable (AWS creds authenticate it), so the
        # gate reports available and the front end re-enables the Claude tabs.
        assert _get_json(port, "/api/provider") == {
            "provider": "bedrock",
            "region": "us-east-1",
            "model": _BEDROCK_MODEL,
            "aiModel": "",
            "effort": "",
            "language": "",
            "claudeAvailable": True,
            "claudeGap": None,
            "claudeHint": "",
        }
        # Switch back to the Anthropic API (the `api-key` provider).
        code, body = _post(port, "/api/provider", {"provider": "api-key"})
        assert code == 200 and body["provider"] == "api-key"
        assert os.environ[ac.PROVIDER_ENV] == "api-key"
        assert _get_json(port, "/api/provider")["provider"] == "api-key"
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


def test_http_provider_select_ant_and_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Anthropic CLI (`ant`, BE-0163) is a first-class SDK provider: selecting it sets
    BAJUTSU_AI_PROVIDER=ant (no model/region), and reachability reflects the CLI's sign-in state —
    reported here as missing since no `ant` binary is installed in CI. Switching back restores
    anthropic. Nothing is written to disk."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    # Deterministic: report the `ant` CLI absent regardless of the CI host (the probe is a subprocess).
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["provider"] == "ant"
        assert os.environ[ac.PROVIDER_ENV] == "ant"
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        assert _get_json(port, "/api/provider") == {
            "provider": "ant",
            "region": "",
            "model": "",
            "aiModel": "",
            "effort": "",
            "language": "",
            "claudeAvailable": False,
            "claudeGap": ac.ANT_CLI_MISSING,
            "claudeHint": ai_availability.message(ac.ANT_CLI_MISSING),
        }
        # Switch back to the Anthropic API (the `api-key` provider).
        code, body = _post(port, "/api/provider", {"provider": "api-key"})
        assert code == 200 and body["provider"] == "api-key"
        assert os.environ[ac.PROVIDER_ENV] == "api-key"
        assert _get_json(port, "/api/provider")["provider"] == "api-key"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_accepts_the_legacy_anthropic_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # BE-0047 shipped the direct-API provider as `anthropic`; a cached UI or old client still POSTs
    # it, so the endpoint canonicalizes it to `api-key` rather than rejecting it as unknown.
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "anthropic"})
        assert code == 200 and body["provider"] == "api-key"
        assert os.environ[ac.PROVIDER_ENV] == "api-key"
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


def test_http_provider_output_language_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0188: the output-language dropdown persists into the process env spawned jobs inherit —
    `ja` sets it, `auto` clears it, and an unknown value is rejected without touching the env."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "ja"})
        assert code == 200 and body["language"] == "ja"
        assert os.environ[ac.LANGUAGE_ENV] == "ja"
        assert _get_json(port, "/api/provider")["language"] == "ja"
        # `auto` is the no-override default: it clears the env rather than storing a value.
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "auto"})
        assert code == 200 and body["language"] == "auto"
        assert ac.LANGUAGE_ENV not in os.environ
        assert _get_json(port, "/api/provider")["language"] == ""
        # An unknown language is a visible 400, and the env is left as it was (cleared above).
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "klingon"})
        assert code == 400 and "language" in body["error"]
        assert ac.LANGUAGE_ENV not in os.environ
    finally:
        server.shutdown()
        server.server_close()
