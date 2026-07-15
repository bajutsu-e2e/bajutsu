"""Structural tests for the project-hub UI (BE-0225 unit 4; the Projects page, BE-0275).

The header switcher ships inlined in the index (its JS in serve.core.mjs); BE-0275 promotes the
former Projects modal into a top-level page whose JS ships as the serve.projects.mjs ES module. Like
the metrics UI tests, these assert the page markup ships (from the index), the modal is retired, and
the module JS wires the add / remove / switch endpoints. The behaviour itself is covered by the
operation/transport tests; here we pin that the surface exists and targets the right endpoints.
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


def test_header_switcher_ships_and_the_page_tab_replaces_the_modal_button(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-testid="nav.projects"' in text  # the header <select> switcher stays
    assert 'data-view="projects"' in text  # the new top-level tab
    assert 'data-testid="nav.projects-view"' in text
    # The modal and its header button are retired into the page.
    assert 'data-testid="nav.open-projects"' not in text
    assert 'data-testid="projects.modal"' not in text


def test_projects_view_and_add_form_ship(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-testid="view.projects"' in text  # the view shell
    assert 'data-testid="projects.host"' in text  # the client-rendered list host
    # The Add form (BE-0275 unit 2).
    assert 'data-testid="projects.add-name"' in text
    assert 'data-testid="projects.add-source"' in text
    assert 'data-testid="projects.add-submit"' in text


def test_js_wires_add_remove_and_switch(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.projects.mjs")
    assert "/api/projects" in text  # POST to add
    assert "sourceSpec" in text  # the single config-source string the Add form sends
    assert "DELETE" in text  # per-row Remove
    assert "switchProject" in text  # per-row Switch reuses the core hub switch
    assert "renderProjectsView" in text


def test_core_js_reveals_the_projects_tab(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.core.mjs")
    # loadProjects reveals the Projects tab (shown for any project count) and drives the switch.
    assert 'data-view="projects"' in text
    assert "loadProjects" in text
    assert "/activate" in text
