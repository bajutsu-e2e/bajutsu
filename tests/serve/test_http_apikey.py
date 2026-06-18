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


def test_http_api_key_set_reveal_and_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trip the Claude API key through the WebUI: unset → set (redacted) → reveal → clear.
    The key is held in the serve process's environment only (in memory) — never written to disk —
    so a spawned job inherits it via os.environ."""
    scn_dir, cfg, runs = project(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # clean start + auto-restore at teardown
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert _get_json(port, "/api/apikey") == {"set": False}
        # Set it: the response redacts the value, and it lands in the process env (not on disk).
        code, body = _post(port, "/api/apikey", {"value": "sk-ant-secret-12345"})
        assert code == 200 and body["set"] is True
        assert body["masked"] == "sk-a…2345" and "secret" not in body["masked"]
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret-12345"
        assert not (tmp_path / ".env").exists()  # nothing is persisted to disk
        # GET is redacted by default; ?reveal=1 returns the full value.
        assert _get_json(port, "/api/apikey") == {"set": True, "masked": "sk-a…2345"}
        assert _get_json(port, "/api/apikey?reveal=1")["value"] == "sk-ant-secret-12345"
        # An empty value clears it.
        code, body = _post(port, "/api/apikey", {"value": ""})
        assert code == 200 and body["set"] is False
        assert _get_json(port, "/api/apikey") == {"set": False}
        assert "ANTHROPIC_API_KEY" not in os.environ
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
