"""Tests for the theme editor contract endpoint (BE-0191 unit 6)."""

from __future__ import annotations

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
