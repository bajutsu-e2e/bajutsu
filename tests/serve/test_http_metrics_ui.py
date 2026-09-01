"""Structural tests for the cross-target comparison UI (BE-0226 unit 3, BE-0404 unit 4).

The comparison dashboard's markup ships inlined in the index; its JS ships as the serve.metrics.mjs
ES module (BE-0247). These assert the markup ships (from the index) and the module JS wires the
comparison endpoint and the read-only drill-down. The aggregation and sorting semantics are covered
by the unit-1/2 operation tests; here we pin that the surface exists and targets the right endpoint.

The surface writes nothing at all since BE-0404 collapsed the project layer: a target is not a
binding, so the per-row Activate that used to rebind the deployment is gone and the cases below pin
that a row only ever opens a read-only history, reachable by keyboard.
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


def test_metrics_tab_and_view_ship(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/")
    assert 'data-view="metrics"' in text  # the header tab
    assert 'data-testid="nav.metrics"' in text
    assert 'data-testid="view.metrics"' in text  # the view shell


def test_the_view_is_named_apart_from_the_prometheus_endpoint(tmp_path: Path) -> None:
    # The tab used to read "Metrics", which named the Prometheus scrape endpoint this same server
    # exposes at /metrics just as well as it named this view (#1720).
    text = _fetch(tmp_path, "/")
    assert ">Comparison</button>" in text
    assert "Prometheus scrape endpoint" in text


def test_js_fetches_the_comparison_endpoint(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # The dashboard reads the unit-2 comparison model and renders it client-side.
    assert "/api/metrics/targets" in text
    assert "loadMetrics" in text


def test_a_row_opens_the_read_only_run_history(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # The drill-down reads the ordinary run list scoped server-side by the run's own `target` stamp,
    # so it reads the same newest-N window of one target the ranking row was computed over. Every
    # label is in view, since the comparison ranks a config's targets rather than one partition.
    assert "'/api/runs?label=*&ranTarget='+encodeURIComponent(name)" in text
    assert "openMetricsDetail" in text
    assert 'data-testid="metrics.detail"' in text


def test_the_drilldown_is_reachable_by_keyboard(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # Plain <tr> elements with a click listener were unreachable without a pointer (#1720). The
    # control is a real button in the name cell, so Enter and Space come from the platform.
    assert 'data-testid="metrics.open"' in text
    assert '<button type="button" class="mopen"' in text


def test_the_row_keeps_its_table_semantics(tmp_path: Path) -> None:
    # role="button" on the <tr> would reach the keyboard too, but it overrides the row's implicit
    # table role, losing row/cell navigation over the ranking. The name button avoids that (#1720).
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # Pin the row's own opening tag: it closes right after `data-name`, which is what proves the
    # <tr> carries neither attribute. Scanning the whole module for "tabindex" would add no
    # coverage and would fail here for an unrelated reason the first time the attribute is wanted
    # elsewhere in it — making the drill-down's run list navigable, say.
    assert '<tr class="mrow" data-testid="metrics.row" data-name="${esc(m.name)}">' in text


def test_the_comparison_writes_nothing(tmp_path: Path) -> None:
    # A target is not a binding, so there is nothing here to rebind (BE-0404 unit 4). The view is a
    # pure read: no switch call, no POST, and no confirm dialog guarding one.
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    assert "switchProject" not in text
    assert "postJSON" not in text
    assert "confirm" not in text


def test_the_label_switcher_ships_and_repoints_every_history_view(tmp_path: Path) -> None:
    # The label filter is on by default, so a deployment must be able to see which partition it is
    # reading and widen it — an invisible default-on filter would silently narrow the history with
    # no control to clear (BE-0404 unit 4).
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        index = _get(port, "/")[1].decode("utf-8")
        core = _get(port, "/serve.core.mjs")[1].decode("utf-8")
        panels = _get(port, "/serve.panels.mjs")[1].decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
    assert 'data-testid="nav.label"' in index
    assert "async function loadLabels(" in core
    assert "all labels" in core
    # One change repoints all three history-backed views, so they cannot disagree about the partition.
    assert "async function onLabelChange(" in panels
    for read in (
        "'/api/runs'+labelParam('?')",
        "'/stats'+labelParam('?')",
        "'/flakiness'+labelParam('?')",
    ):
        assert read in panels
