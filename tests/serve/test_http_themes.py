"""The index folds drop-in themes into the inlined stylesheet and exposes the manifest (BE-0191)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from _shared import _get, _serve, project

from bajutsu import serve as srv


def _themes_global(body: bytes) -> list[dict[str, str]]:
    """The `window.__bajutsuThemes=[…]` array the index inlines for the picker."""
    m = re.search(rb"window\.__bajutsuThemes=(\[.*?\]);window\.__bajutsuDefaultTheme=", body)
    assert m, "index did not expose window.__bajutsuThemes"
    return json.loads(m.group(1))


def _picker_options(body: bytes) -> list[str]:
    """The `value=` of every `<option>` inside the `nav.theme-picker` `<select>` (in order)."""
    text = body.decode("utf-8")
    m = re.search(r'<select[^>]*data-testid="nav\.theme-picker".*?</select>', text, re.DOTALL)
    assert m, "index did not render the nav.theme-picker <select>"
    return re.findall(r'<option[^>]*\bvalue="([^"]*)"', m.group(0))


def test_index_exposes_builtin_manifest_by_default(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        _, body, _ = _get(port, "/")
        ids = [t["id"] for t in _themes_global(body)]
        assert ids == ["midnight", "daylight"]
        assert b"window.__bajutsuDefaultTheme=null" in body
    finally:
        server.shutdown()
        server.server_close()


def test_index_folds_in_and_lists_a_drop_in_theme(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "solarized.css").write_text(
        "/* bajutsu-theme name: Solarized Dark kind: dark */\n"
        '[data-theme="solarized"]{--bg:#002b36;--fg:#839496}\n',
        encoding="utf-8",
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, themes_dir=themes
    )
    server, port = _serve(state)
    try:
        _, body, _ = _get(port, "/")
        text = body.decode("utf-8")
        assert '[data-theme="solarized"]' in text  # the drop-in CSS is inlined
        assert "#002b36" in text
        manifest = {t["id"]: t for t in _themes_global(body)}
        assert manifest["solarized"] == {
            "id": "solarized",
            "name": "Solarized Dark",
            "kind": "dark",
        }
        assert list(manifest) == ["midnight", "daylight", "solarized"]  # built-ins first
    finally:
        server.shutdown()
        server.server_close()


def test_index_renders_picker_with_an_option_per_theme(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "solarized.css").write_text(
        "/* bajutsu-theme name: Solarized Dark kind: dark */\n"
        '[data-theme="solarized"]{--bg:#002b36;--fg:#839496}\n',
        encoding="utf-8",
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, themes_dir=themes
    )
    server, port = _serve(state)
    try:
        _, body, _ = _get(port, "/")
        # The picker replaces the binary toggle: one <option> per registered theme, grouped by kind
        # (dark group first), each option value being the theme id a scenario selects deterministically.
        assert _picker_options(body) == ["midnight", "solarized", "daylight"]
        # The old two-state toggle is gone — nothing should still reference it.
        assert b'data-testid="nav.theme-toggle"' not in body
    finally:
        server.shutdown()
        server.server_close()


def test_index_reflects_configured_default_theme(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, default_theme="daylight"
    )
    server, port = _serve(state)
    try:
        _, body, _ = _get(port, "/")
        assert b'window.__bajutsuDefaultTheme="daylight"' in body
    finally:
        server.shutdown()
        server.server_close()


def test_index_warns_on_unknown_default_theme(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    state = srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, default_theme="nonexistent"
    )
    import bajutsu.serve.handler as _h

    _h._index_html.cache_clear()
    with caplog.at_level("WARNING"):
        _h._index_html(state.themes_dir, state.default_theme)
    assert "nonexistent" in caplog.text
    assert "unthemed" in caplog.text
    _h._index_html.cache_clear()
