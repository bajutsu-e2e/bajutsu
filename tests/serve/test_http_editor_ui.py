"""Tests for the Editor tab UI structure (BE-0013, Slice 2).

Structural tests only — the HTML/CSS/JS is inlined, so assert the markup ships.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _index_text(tmp_path: Path) -> str:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return _get(port, "/")[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_editor_tab_button_exists(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'data-view="editor"' in text
    assert 'data-testid="nav.editor"' in text
    assert ">Editor</button>" in text


def test_editor_view_exists(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="view-editor"' in text
    assert 'data-testid="view.editor"' in text


def test_editor_has_two_pane_layout(tmp_path: Path) -> None:
    """Editor uses the same left/gutter/rec-stack two-pane pattern as Capture."""
    text = _index_text(tmp_path)
    assert 'id="edt-target"' in text
    assert 'id="edt-scenario"' in text
    assert 'id="edt-run"' in text


def test_editor_has_screenshot_picker(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="edt-screenshot"' in text
    assert "edt-feedback" in text
    assert "edt-rung" in text
    assert "edt-sel" in text


def test_editor_has_step_navigation(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="edt-prev"' in text
    assert 'id="edt-next"' in text
    assert 'id="edt-step-label"' in text


def test_editor_has_viewswitch(tmp_path: Path) -> None:
    """Phone-tier pane switcher ships for the Editor tab."""
    text = _index_text(tmp_path)
    assert 'data-testid="editor.switch"' in text


def test_editor_js_wires_resolve(tmp_path: Path) -> None:
    """The JS contains the resolve-pick fetch to /api/scenario/resolve."""
    text = _index_text(tmp_path)
    assert "/api/scenario/resolve" in text


def test_editor_js_wires_step_load(tmp_path: Path) -> None:
    """The JS loads scenario steps with run artifacts via the extended API."""
    text = _index_text(tmp_path)
    assert "runId=" in text
    assert "scenario=" in text
    assert "edt-screenshot" in text


# ---------------------------------------------------------------------------
# Slice 3: structured editing + save
# ---------------------------------------------------------------------------


def test_editor_has_yaml_textarea(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="edt-yaml"' in text
    assert "edt-yamlpanel" in text


def test_editor_has_save_button(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="edt-save"' in text


def test_editor_has_apply_button(tmp_path: Path) -> None:
    """The Apply button writes a resolved selector into the YAML."""
    text = _index_text(tmp_path)
    assert 'id="edt-apply"' in text


def test_editor_js_wires_save(tmp_path: Path) -> None:
    """The JS posts to /api/scenario for save."""
    text = _index_text(tmp_path)
    assert "edt-save" in text
    assert "edt-yaml" in text
