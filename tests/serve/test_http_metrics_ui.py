"""Structural tests for the cross-project metrics comparison UI (BE-0226 unit 3).

The comparison dashboard is inlined HTML/CSS/JS with no JS test harness, so — like the project-hub
UI tests — these assert the markup ships and the JS wires the comparison endpoint and the deep-link
into a project's single-config dashboard. The aggregation and sorting semantics are covered by the
unit-1/2 operation tests; here we pin that the surface exists and targets the right endpoint.
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


def test_metrics_tab_and_view_ship(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    assert 'data-view="metrics"' in text  # the header tab
    assert 'data-testid="nav.metrics"' in text
    assert 'data-testid="view.metrics"' in text  # the view shell


def test_js_fetches_the_comparison_endpoint(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    # The dashboard reads the unit-2 comparison model and renders it client-side.
    assert "/api/metrics/projects" in text
    assert "loadMetrics" in text


def test_js_deep_links_into_the_single_config_dashboard(tmp_path: Path) -> None:
    text = _index_text(tmp_path)
    # A row click rebinds the project through the hub switcher and lands on its Stats dashboard.
    assert "switchProject" in text
    assert "goStats" in text
