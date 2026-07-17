"""Tests for the unified Author tab UI structure (BE-0098).

BE-0098 collapses the former Capture (BE-0012) and Editor (BE-0013) tabs, and the
Enrich button+panel (BE-0014), into one "Author" tab with a Capture / Edit / Enrich
mode switcher over a single open scenario. The markup ships inlined in the index; the JS
ships as the serve.author.mjs ES module (BE-0247). These are structural tests: assert the
unified markup (from the index) and that the module JS still wires the (unchanged) endpoints.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _serve, project

from bajutsu import serve as srv


def _fetch(tmp_path: Path, route: str) -> str:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        return _get(port, route)[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def _index_text(tmp_path: Path) -> str:
    return _fetch(tmp_path, "/")


def _author_js(tmp_path: Path) -> str:
    return _fetch(tmp_path, "/serve.author.mjs")


# ---------------------------------------------------------------------------
# The unified Author tab replaces the separate Capture / Editor top-level tabs.
# ---------------------------------------------------------------------------


def test_author_tab_button_exists(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'data-view="author"' in text
    assert 'data-testid="nav.author"' in text
    assert ">Author</button>" in text


def test_author_view_exists(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="view-author"' in text
    assert 'data-testid="view.author"' in text


def test_old_capture_editor_tabs_removed(tmp_path: Path) -> None:
    """The separate Capture and Editor top-level tabs and views are gone."""
    text = _index_text(tmp_path)
    for gone in (
        'data-view="capture"',
        'data-view="editor"',
        'data-testid="nav.capture"',
        'data-testid="nav.editor"',
        'id="view-capture"',
        'id="view-editor"',
    ):
        assert gone not in text, gone


# ---------------------------------------------------------------------------
# Mode switcher: Capture / Edit / Enrich over the one open scenario.
# ---------------------------------------------------------------------------


def test_author_has_mode_switcher(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'data-testid="author.mode"' in text
    for mode in ("capture", "edit", "enrich"):
        assert f'data-mode="{mode}"' in text, mode
        # The per-mode testids are the stable hooks an E2E scenario clicks to switch mode.
        assert f'data-testid="author.mode-{mode}"' in text, mode


def test_author_mode_visibility_mechanism_ships(tmp_path: Path) -> None:
    """Mode switching hinges on toggling ``hidden`` on the mode-group classes, plus a CSS rule
    that makes ``[hidden]`` beat their display rules — a load-bearing line that must ship."""
    text = _index_text(tmp_path)
    for cls in ("au-cap", "au-edit", "au-enrich", "au-loadrow"):
        assert f"{cls}" in text, cls
    assert (
        ".au-cap[hidden],.au-edit[hidden],.au-enrich[hidden],.au-loadrow[hidden]{display:none}"
        in text
    )


# ---------------------------------------------------------------------------
# Shared state: target + scenario + run selection, steps, YAML, Save.
# ---------------------------------------------------------------------------


def test_author_has_shared_scenario_controls(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    for el in ('id="au-target"', 'id="au-scenario"', 'id="au-run"', 'id="au-load"'):
        assert el in text, el


def test_author_has_shared_yaml_and_save(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="au-yaml"' in text
    assert 'id="au-save"' in text
    assert "au-yamlpanel" in text


def test_author_has_shared_steps_list(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="au-steplist"' in text


def test_author_has_viewswitch(tmp_path: Path) -> None:
    """Phone-tier pane switcher (Form / YAML / Screen / Steps) ships for the Author tab.

    The YAML tab (BE-0263) gives the editor its own narrow-tier pane, now that it is a
    first-class tile pane rather than a card stacked inside the form column.
    """
    text = _index_text(tmp_path)
    assert 'data-testid="author.switch"' in text
    for pane in ("form", "yaml", "screen", "steps"):
        assert f'data-pane="{pane}"' in text, pane


# ---------------------------------------------------------------------------
# Per-mode controls: Capture, Edit, and Enrich each keep their controls.
# ---------------------------------------------------------------------------


def test_author_has_shared_screenshot_picker(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    for el in ('id="au-screenshot"', "au-feedback", "au-rung", "au-sel"):
        assert el in text, el


def test_author_capture_mode_controls(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="au-start"' in text
    assert 'id="au-finish"' in text
    assert 'name="au-mode"' in text  # tap / type action radios


def test_author_edit_mode_controls(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    for el in ('id="au-prev"', 'id="au-next"', 'id="au-step-label"', 'id="au-apply"'):
        assert el in text, el


def test_author_edit_mode_live_session_controls(tmp_path: Path) -> None:
    """Edit ships a live-session picker (BE-0262): Start/Stop controls tagged au-edit so the mode
    switcher shows them only in Edit, giving a working picker with no prior run."""
    text = _index_text(tmp_path)
    for el in (
        'id="au-live-start"',
        'id="au-live-stop"',
        'data-testid="author.live-start"',
        'data-testid="author.live-stop"',
    ):
        assert el in text, el
    # The controls belong to the Edit mode group, so setMode reveals them only in Edit.
    assert '<div class="au-live au-edit' in text


def test_author_enrich_mode_controls(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'id="au-enrich"' in text
    assert 'id="au-enrich-panel"' in text
    assert 'id="au-enrich-accept"' in text
    assert 'id="au-enrich-dismiss"' in text


def test_author_codegen_controls(tmp_path: Path) -> None:
    """The codegen export (BE-0137): emit selector, Generate button, and a code viewer with
    copy / download."""
    text = _index_text(tmp_path)
    for el in (
        'id="au-emit"',
        'id="au-codegen"',
        'id="au-codegen-panel"',
        'id="au-codegen-code"',
        'id="au-codegen-copy"',
        'id="au-codegen-download"',
    ):
        assert el in text, el


# ---------------------------------------------------------------------------
# The JS still wires the (unchanged) endpoints each mode uses.
# ---------------------------------------------------------------------------


def test_author_js_wires_capture_endpoints(tmp_path: Path) -> None:
    text = _author_js(tmp_path)
    for ep in ("/api/capture/start", "/api/capture/mark", "/api/capture/finish"):
        assert ep in text, ep


def test_author_js_wires_editor_endpoints(tmp_path: Path) -> None:
    text = _author_js(tmp_path)
    assert "/api/scenario/resolve" in text
    assert "runId=" in text
    assert "scenario=" in text


def test_author_js_wires_live_session_endpoints(tmp_path: Path) -> None:
    """The live Edit picker reuses Capture's session endpoints (BE-0262): boot + screenshot to open,
    resolve to pick, close to tear down without saving."""
    text = _author_js(tmp_path)
    for ep in (
        "/api/capture/start",
        "/api/capture/screenshot",
        "/api/capture/resolve",
        "/api/capture/close",
    ):
        assert ep in text, ep


def test_author_js_states_no_run_no_session_prompt(tmp_path: Path) -> None:
    """With no run and no live session, Edit states how to get a picker rather than sitting inert
    (BE-0262 unit 3): the placeholder branches on the Run selection and points at the live session."""
    text = _author_js(tmp_path)
    assert "to pick elements on the current screen" in text
    assert "$('#au-run').value" in text


def test_author_js_wires_save_and_enrich(tmp_path: Path) -> None:
    text = _author_js(tmp_path)
    assert "/api/scenario" in text
    assert "/api/enrich" in text


def test_author_js_wires_codegen(tmp_path: Path) -> None:
    text = _author_js(tmp_path)
    assert "/api/codegen" in text


# ---------------------------------------------------------------------------
# Tiling layout (BE-0263): Author joins Record/Replay/Crawl in initTiling's SPECS,
# so its panes resize / split / swap and persist, instead of a fixed CSS grid.
# ---------------------------------------------------------------------------


def test_author_registered_in_tiling_specs(tmp_path: Path) -> None:
    """The tiler's SPECS lists view-author with the editor as its own pane (BE-0263)."""
    text = _author_js(tmp_path)
    assert "'view-author'" in text
    # The four panes the spec addresses, each by the selector the tiler grabs.
    assert ".rec-stack .yamlpanel" in text
    assert ".rec-stack .au-steps-card" in text
    assert ".rec-stack .au-screen-card" in text


def test_author_is_block_level_for_tiling(tmp_path: Path) -> None:
    """The fixed #view-author grid is gone; it is block-level like the other tiled views, so
    the tiler's .tile-root fills the <main> (BE-0263)."""
    text = _index_text(tmp_path)
    assert "#view-author:not([hidden])" in text
    # The old bespoke fixed-column grid must not linger.
    assert "#view-author{grid-template-columns" not in text
