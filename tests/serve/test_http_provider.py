"""Tests for the bajutsu serve AI-provider endpoint (/api/provider), real ThreadingHTTPServer.

Since BE-0229 the endpoint stores the selection *per organization* (`ProviderSettingsManager.settings`
keyed by org, held as `ServeState.providers`) and materializes it into a per-job env overlay at dispatch
— never into the shared `os.environ` — so a hosted, multi-tenant serve resolves provider/model/effort
per org instead of
one operator's save changing everyone's AI runs. These tests assert the selection round-trips
through GET and resolves into the org's overlay (`resolve_provider_env`), and crucially that the
process env is never written (the tenant-isolation guarantee).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import ai_availability
from bajutsu import ai_config as aic
from bajutsu import anthropic_client as ac
from bajutsu import serve as srv
from bajutsu.serve.operations.config import resolve_provider_env
from bajutsu.serve.orgs import DEFAULT_ORG

_BEDROCK_MODEL = "global.anthropic.claude-opus-4-6-v1"

_PROVIDER_ENV_VARS = (
    aic.PROVIDER_ENV,
    aic.BEDROCK_MODEL_ENV,
    aic.MODEL_ENV,
    aic.EFFORT_ENV,
    ac.ANTHROPIC_KEY_ENV,
    aic.LANGUAGE_ENV,
    "AWS_REGION",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env() -> Iterator[None]:
    """Give every test a clean launch env and fully restore it afterwards.

    Since BE-0229 the endpoint no longer writes `os.environ`, but the env is still the fallback layer
    that `provider_info` / the overlay read when an org has selected nothing — so a stray
    `BAJUTSU_AI_*` from another test would perturb these assertions. Snapshot/restore keeps each test
    starting from a clean env (and cleans up anything a helper sets), the same isolation the sibling
    persistence tests rely on."""
    saved = {var: os.environ.get(var) for var in _PROVIDER_ENV_VARS}
    for var in _PROVIDER_ENV_VARS:
        os.environ.pop(var, None)
    try:
        yield
    finally:
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value


def _clean_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op: env isolation is handled by the autouse _isolate_provider_env fixture.

    Kept for compatibility with the call sites that still pass monkeypatch to signal "start from a
    clean env" — the fixture already does that for every test."""


def _no_process_env() -> None:
    """The tenant-isolation invariant BE-0229 introduces: a save never mutates the shared process
    env, so nothing leaks between orgs' jobs."""
    for var in (
        aic.PROVIDER_ENV,
        aic.MODEL_ENV,
        aic.BEDROCK_MODEL_ENV,
        aic.EFFORT_ENV,
        aic.LANGUAGE_ENV,
    ):
        assert var not in os.environ


def test_http_provider_select_bedrock_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip the AI provider through the WebUI: default anthropic → bedrock (region + model
    resolve into the org's per-job overlay) → back to anthropic. Nothing is written to the process
    env or to disk."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
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
            "providers": {"api-key": {"model": "", "effort": "", "region": ""}},
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
        # The selection resolves into the org's per-job env overlay (BE-0229), never the process env.
        overlay = resolve_provider_env(state, DEFAULT_ORG)
        assert overlay[aic.PROVIDER_ENV] == "bedrock"
        assert overlay["AWS_REGION"] == "us-east-1"
        assert overlay[aic.BEDROCK_MODEL_ENV] == _BEDROCK_MODEL
        _no_process_env()
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
            "providers": {
                "bedrock": {"model": _BEDROCK_MODEL, "effort": "", "region": "us-east-1"}
            },
            "claudeAvailable": True,
            "claudeGap": None,
            "claudeHint": "",
        }
        # Switch back to the Anthropic API (the `api-key` provider).
        code, body = _post(port, "/api/provider", {"provider": "api-key"})
        assert code == 200 and body["provider"] == "api-key"
        assert resolve_provider_env(state, DEFAULT_ORG)[aic.PROVIDER_ENV] == "api-key"
        assert _get_json(port, "/api/provider")["provider"] == "api-key"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_bedrock_requires_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bedrock needs a provider-prefixed model id; without one the request is rejected and no
    provider selection is stored."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "bedrock", "region": "us-east-1"})
        assert code == 400 and "model" in body["error"]
        assert resolve_provider_env(state, DEFAULT_ORG) == {}  # nothing selected
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_select_ant_and_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Anthropic CLI (`ant`, BE-0163) is a first-class SDK provider: selecting it stores
    provider=ant (no model/region) in the org's selection, and reachability reflects the CLI's
    sign-in state — reported here as missing since no `ant` binary is installed in CI. Switching back
    restores anthropic. Nothing is written to the process env or to disk."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    # Deterministic: report the `ant` CLI absent regardless of the CI host (the probe is a subprocess).
    monkeypatch.setattr(ac.shutil, "which", lambda _exe: None)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "ant"})
        assert code == 200 and body["provider"] == "ant"
        assert resolve_provider_env(state, DEFAULT_ORG)[aic.PROVIDER_ENV] == "ant"
        _no_process_env()
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk
        assert _get_json(port, "/api/provider") == {
            "provider": "ant",
            "region": "",
            "model": "",
            "aiModel": "",
            "effort": "",
            "language": "",
            "providers": {"ant": {"model": "", "effort": "", "region": ""}},
            "claudeAvailable": False,
            "claudeGap": ac.ANT_CLI_MISSING,
            "claudeHint": ai_availability.message(ac.ANT_CLI_MISSING),
        }
        # Switch back to the Anthropic API (the `api-key` provider).
        code, body = _post(port, "/api/provider", {"provider": "api-key"})
        assert code == 200 and body["provider"] == "api-key"
        assert resolve_provider_env(state, DEFAULT_ORG)[aic.PROVIDER_ENV] == "api-key"
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
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "anthropic"})
        assert code == 200 and body["provider"] == "api-key"
        assert resolve_provider_env(state, DEFAULT_ORG)[aic.PROVIDER_ENV] == "api-key"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_remembers_settings_per_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0183: each provider keeps its own model/effort. Configuring claude-code, then saving
    api-key (which the old shared-slot design would have wiped), then switching back leaves
    claude-code's model/effort intact — read from the per-provider map GET /api/provider returns."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, _ = _post(
            port,
            "/api/provider",
            {"provider": "claude-code", "aiModel": "claude-opus-4-6", "effort": "high"},
        )
        assert code == 200
        # Saving a different provider must not disturb claude-code's remembered slot.
        code, _ = _post(
            port, "/api/provider", {"provider": "api-key", "aiModel": "claude-sonnet-4-6"}
        )
        assert code == 200
        providers = _get_json(port, "/api/provider")["providers"]
        assert providers["claude-code"] == {
            "model": "claude-opus-4-6",
            "effort": "high",
            "region": "",
        }
        assert providers["api-key"] == {"model": "claude-sonnet-4-6", "effort": "", "region": ""}
        # Switching back materializes claude-code's remembered slot into the org's job overlay.
        code, _ = _post(
            port,
            "/api/provider",
            {"provider": "claude-code", "aiModel": "claude-opus-4-6", "effort": "high"},
        )
        assert code == 200
        overlay = resolve_provider_env(state, DEFAULT_ORG)
        assert overlay[aic.PROVIDER_ENV] == "claude-code"
        assert overlay[aic.MODEL_ENV] == "claude-opus-4-6"
        assert overlay[aic.EFFORT_ENV] == "high"
    finally:
        server.shutdown()
        server.server_close()


def test_http_provider_write_scopes_to_the_selected_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0183: a Bedrock save writes only Bedrock's slot; the api-key slot set earlier is untouched."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, _ = _post(
            port, "/api/provider", {"provider": "api-key", "aiModel": "claude-sonnet-4-6"}
        )
        assert code == 200
        code, _ = _post(
            port,
            "/api/provider",
            {
                "provider": "bedrock",
                "region": "us-east-1",
                "model": _BEDROCK_MODEL,
                "effort": "low",
            },
        )
        assert code == 200
        providers = _get_json(port, "/api/provider")["providers"]
        assert providers["api-key"] == {"model": "claude-sonnet-4-6", "effort": "", "region": ""}
        assert providers["bedrock"] == {
            "model": _BEDROCK_MODEL,
            "effort": "low",
            "region": "us-east-1",
        }
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
    """BE-0188: the output-language dropdown persists into the org's job overlay — `ja` sets it,
    `auto` clears it, and an unknown value is rejected without changing the stored selection."""
    scn_dir, cfg, runs = project(tmp_path)
    _clean_provider_env(monkeypatch)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "ja"})
        assert code == 200 and body["language"] == "ja"
        assert resolve_provider_env(state, DEFAULT_ORG)[aic.LANGUAGE_ENV] == "ja"
        assert _get_json(port, "/api/provider")["language"] == "ja"
        # `auto` is the no-override default: it stores no language, so the overlay omits it.
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "auto"})
        assert code == 200 and body["language"] == "auto"
        assert aic.LANGUAGE_ENV not in resolve_provider_env(state, DEFAULT_ORG)
        assert _get_json(port, "/api/provider")["language"] == ""
        # An unknown language is a visible 400, and the stored language is left as it was (cleared).
        code, body = _post(port, "/api/provider", {"provider": "api-key", "language": "klingon"})
        assert code == 400 and "language" in body["error"]
        assert aic.LANGUAGE_ENV not in resolve_provider_env(state, DEFAULT_ORG)
    finally:
        server.shutdown()
        server.server_close()
