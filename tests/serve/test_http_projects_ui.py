"""Structural tests for the project-hub UI (BE-0225 unit 4).

The switcher + projects list markup ships inlined in the index; its JS ships in the serve.core.mjs
ES module (BE-0247). Like the Author UI tests, these assert the markup ships (from the index) and
the module JS wires the activate endpoint. The switching behaviour itself is covered by the
operation/transport tests; here we pin that the surface exists and targets the right endpoint.
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


def test_header_switcher_and_projects_button_ship(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-testid="nav.projects"' in text  # the header <select> switcher
    assert 'data-testid="nav.open-projects"' in text  # opens the projects list


def test_projects_modal_and_list_ship(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-testid="projects.modal"' in text
    assert 'data-testid="projects.list"' in text


def test_js_wires_the_activate_endpoint(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.core.mjs")
    # The switcher and each row's Run activate a project through the unit-3 endpoint.
    assert "/activate" in text
    assert "loadProjects" in text
