"""Tests for the theme editor contract endpoint (BE-0191 unit 6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from _shared import _get_json, _serve, project

from bajutsu import serve as srv
from bajutsu.serve.operations import theme_editor


def test_get_theme_contract_reads_real_file():
    """The contract endpoint reads the real serve.themes.css and returns 200 with tokens.

    Guards the path-resolution bug (a missing .parent hop makes it always 500) and the
    default-value regex (the CSS uses double-quoted [data-theme="midnight"]).
    """
    state = None  # get_theme_contract does not use state; it reads a bundled file.
    payload, status = theme_editor.get_theme_contract(state)  # type: ignore[arg-type]
    assert status == 200
    assert "error" not in payload
    # The real contract defines the documented color and motion tokens.
    assert "--bg" in payload["colors"]
    assert "--acc" in payload["colors"]
    assert "--motion-view" in payload["transitions"]


def test_get_theme_contract_populates_defaults():
    """Defaults come from the :root/midnight block (double-quoted selector)."""
    payload, status = theme_editor.get_theme_contract(None)  # type: ignore[arg-type]
    assert status == 200
    # --bg's midnight default is a concrete hex, not empty.
    assert payload["colors"]["--bg"]["default"]
    assert payload["colors"]["--bg"]["default"].startswith("#")
    # A motion duration default is filled too.
    assert payload["transitions"]["--motion-view"]["default"]


def test_get_theme_contract_read_failure_returns_500(tmp_path: Path) -> None:
    """A read failure (missing bundled file) returns a 500 payload, not a 200 with an error body."""
    missing = tmp_path / "nonexistent.css"
    with patch.object(theme_editor, "_CONTRACT_PATH", missing):
        payload, status = theme_editor.get_theme_contract(None)  # type: ignore[arg-type]
    assert status == 500
    assert "error" in payload


def test_api_theme_contract_route(tmp_path: Path) -> None:
    """GET /api/themecontract serves the contract JSON end to end (like sibling routes)."""
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        payload = _get_json(port, "/api/themecontract")
        assert "--bg" in payload["colors"]
        assert "--motion-view" in payload["transitions"]
        assert payload["colors"]["--bg"]["default"].startswith("#")
    finally:
        server.shutdown()
        server.server_close()
