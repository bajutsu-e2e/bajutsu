"""Tests for the bajutsu serve API-key set/reveal/clear endpoints (real ThreadingHTTPServer)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _shared import (
    _get_json,
    _post,
    _serve,
    project,
)

from bajutsu import serve as srv


def test_http_api_key_set_describe_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip the Claude API key through the WebUI: unset → set (masked) → clear. Write-once
    (BE-0136): no endpoint ever returns the plaintext, only a masked preview. The key is held in the
    serve process's environment only (in memory) — never on disk — so a spawned job inherits it."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # clean start + auto-restore at teardown
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert _get_json(port, "/api/apikey") == {"set": False}
        # Set it: the response masks the value, and it lands in the process env (not on disk).
        code, body = _post(port, "/api/apikey", {"value": "sk-ant-secret-12345"})
        assert code == 200 and body["set"] is True
        assert body["masked"] == "sk-a…2345" and "secret" not in body["masked"]
        assert "value" not in body  # the mutating side never echoes the plaintext back
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-12345"
        assert not (tmp_path / ".env").exists()  # nothing is persisted to disk
        # GET is masked only — there is no reveal, and the plaintext is never a field, for any query.
        assert _get_json(port, "/api/apikey") == {"set": True, "masked": "sk-a…2345"}
        assert "value" not in _get_json(port, "/api/apikey?reveal=1")
        # An empty value clears it.
        code, body = _post(port, "/api/apikey", {"value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/apikey") == {"set": False}
        assert "ANTHROPIC_API_KEY" not in os.environ
    finally:
        server.shutdown()
        server.server_close()


def test_http_claude_code_token_set_describe_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0215: round-trip the `claude-code` OAuth token through the WebUI — the second named
    write-once secret. Same contract as the API key: no endpoint returns the plaintext, only a
    masked preview, and the value materializes into CLAUDE_CODE_OAUTH_TOKEN (its own var, not the
    API key's) so a spawned job inherits the credential the `claude` CLI reads."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert _get_json(port, "/api/claudecodetoken") == {"set": False}
        code, body = _post(port, "/api/claudecodetoken", {"value": "sk-ant-oat01-secret-12345"})
        assert code == 200 and body["set"] is True
        assert body["masked"] == "sk-a…2345" and "secret" not in body["masked"]
        assert "value" not in body
        # Lands under its own var, not the API key's — the two credentials are independent.
        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-secret-12345"
        assert "ANTHROPIC_API_KEY" not in os.environ
        assert not (tmp_path / ".env").exists()
        assert _get_json(port, "/api/claudecodetoken") == {"set": True, "masked": "sk-a…2345"}
        assert "value" not in _get_json(port, "/api/claudecodetoken?reveal=1")
        code, body = _post(port, "/api/claudecodetoken", {"value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/claudecodetoken") == {"set": False}
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
    finally:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        server.shutdown()
        server.server_close()


def test_http_claude_code_token_rejects_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/claudecodetoken", {"value": "oat with spaces"})
        assert code == 400 and "whitespace" in body["error"]
        assert _get_json(port, "/api/claudecodetoken") == {"set": False}
    finally:
        server.shutdown()
        server.server_close()


def test_http_git_credential_set_describe_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0224: round-trip the Git config-source credential through the WebUI — the third named
    write-once secret. Same contract as the API key: no endpoint returns the plaintext, only a masked
    preview, and the value materializes into BAJUTSU_GIT_CONFIG_TOKEN (a bajutsu-owned var, not the
    operator's GITHUB_TOKEN) so the in-process private-repo fetch (and a spawned job) reads it."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "operator-exported-token")  # must survive a UI clear
    monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        # An operator's ambient GITHUB_TOKEN is NOT reported as a UI-set credential.
        assert _get_json(port, "/api/gitcredential") == {"set": False}
        code, body = _post(port, "/api/gitcredential", {"value": "github_pat_secret_12345"})
        assert code == 200 and body["set"] is True
        assert body["masked"] == "gith…2345" and "secret" not in body["masked"]
        assert "value" not in body  # the mutating side never echoes the plaintext back
        assert os.environ["BAJUTSU_GIT_CONFIG_TOKEN"] == "github_pat_secret_12345"
        assert os.environ["GITHUB_TOKEN"] == "operator-exported-token"  # untouched by the UI write
        assert not (tmp_path / ".env").exists()  # nothing is persisted to disk
        assert _get_json(port, "/api/gitcredential") == {"set": True, "masked": "gith…2345"}
        assert "value" not in _get_json(port, "/api/gitcredential?reveal=1")
        code, body = _post(port, "/api/gitcredential", {"value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/gitcredential") == {"set": False}
        assert "BAJUTSU_GIT_CONFIG_TOKEN" not in os.environ
        assert os.environ["GITHUB_TOKEN"] == "operator-exported-token"  # clear didn't pop it
    finally:
        monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
        server.shutdown()
        server.server_close()


def test_http_git_credential_rejects_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("BAJUTSU_GIT_CONFIG_TOKEN", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/gitcredential", {"value": "tok with spaces"})
        assert code == 400 and "whitespace" in body["error"]
        assert _get_json(port, "/api/gitcredential") == {"set": False}
    finally:
        server.shutdown()
        server.server_close()


def test_http_api_key_rejects_whitespace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/apikey", {"value": "sk ant with spaces"})
        assert code == 400 and "whitespace" in body["error"]
        assert _get_json(port, "/api/apikey") == {"set": False}
    finally:
        server.shutdown()
        server.server_close()


# --- BE-0097: set_api_key honours the config's ai.keyEnv ---


def test_http_api_key_honours_key_env_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0097: when the bound config declares `ai.keyEnv`, set_api_key writes the key under that
    env var — not the hardcoded ANTHROPIC_API_KEY — so a spawned job inherits the right name."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: a\n  steps:\n    - tap: { id: x }\n")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults:\n"
        "  backend: [ios]\n"
        "  ai:\n"
        "    keyEnv: MY_CUSTOM_KEY\n"
        "targets:\n"
        "  demo: { bundleId: com.example.demo }\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MY_CUSTOM_KEY", raising=False)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        code, body = _post(port, "/api/apikey", {"value": "sk-custom-12345"})
        assert code == 200 and body["set"] is True
        assert os.environ.get("MY_CUSTOM_KEY") == "sk-custom-12345"
        # The default ANTHROPIC_API_KEY should NOT be set — the config named a different var.
        assert "ANTHROPIC_API_KEY" not in os.environ
        # GET should read the custom var too.
        assert _get_json(port, "/api/apikey")["set"] is True
        # Clear: removes the custom var.
        _post(port, "/api/apikey", {"value": ""})
        assert "MY_CUSTOM_KEY" not in os.environ
    finally:
        server.shutdown()
        server.server_close()


def test_http_api_key_rejects_unsafe_key_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BE-0097: a keyEnv that names a system variable (e.g. PATH) is ignored — the serve UI falls
    back to ANTHROPIC_API_KEY so it cannot overwrite critical env vars."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text("- name: a\n  steps:\n    - tap: { id: x }\n")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults:\n"
        "  backend: [ios]\n"
        "  ai:\n"
        "    keyEnv: PATH\n"
        "targets:\n"
        "  demo: { bundleId: com.example.demo }\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    original_path = os.environ.get("PATH", "")
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        _post(port, "/api/apikey", {"value": "sk-test-12345"})
        # PATH must NOT have been overwritten.
        assert os.environ.get("PATH") == original_path
        # Falls back to ANTHROPIC_API_KEY.
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-test-12345"
    finally:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        server.shutdown()
        server.server_close()
