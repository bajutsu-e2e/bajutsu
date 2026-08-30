"""Structural tests for the cross-project comparison UI (BE-0226 unit 3).

The comparison dashboard's markup ships inlined in the index; its JS ships as the serve.metrics.mjs
ES module (BE-0247). Like the project-hub UI tests, these assert the markup ships (from the index)
and the module JS wires the comparison endpoint and the read-only drill-down. The aggregation and
sorting semantics are covered by the unit-1/2 operation tests; here we pin that the surface exists
and targets the right endpoint.

Since #1720 the surface also has to keep navigation and activation apart, so the cases below pin the
split itself: a row opens the per-project run history and nothing else, the rows are reachable by
keyboard, and rebinding the deployment takes an explicit confirmed button whose refusal names the
right it needs.
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
    assert "/api/metrics/projects" in text
    assert "loadMetrics" in text


def test_a_row_opens_the_read_only_run_history(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # The drill-down reads the project hub's existing per-project runs route — no new server surface.
    assert "'/api/projects/'+encodeURIComponent(name)+'/runs'" in text
    assert "openMetricsDetail" in text
    assert 'data-testid="metrics.detail"' in text


def test_a_row_no_longer_activates_the_project(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # The row's own handler opens the detail; the only switchProject call sits behind the confirmed
    # Activate button. `goStats` was the deep-link option that made a row click rebind the config.
    assert "goStats" not in text
    assert "switchProject(tr.dataset.name" not in text
    assert text.count("await switchProject(name)") == 1


def test_the_drilldown_is_reachable_by_keyboard(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # Plain <tr> elements with a click listener were unreachable without a pointer (#1720). The
    # control is a real button in the name cell, so Enter and Space come from the platform.
    assert 'data-testid="metrics.open"' in text
    assert '<button type="button" class="mopen"' in text


def test_the_row_keeps_its_table_semantics(tmp_path: Path) -> None:
    # role="button" on the <tr> would reach the keyboard too, but it overrides the row's implicit
    # table role — losing row/cell navigation over the ranking — and nests the row's own Activate
    # button inside an ARIA button, which ARIA forbids. The name button avoids both (#1720).
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    # Pin the row's own opening tag: it closes right after `data-name`, which is what proves the
    # <tr> carries neither attribute. Scanning the whole module for "tabindex" would add no
    # coverage and would fail here for an unrelated reason the first time the attribute is wanted
    # elsewhere in it — making the drill-down's run list navigable, say.
    assert '<tr class="mrow" data-testid="metrics.row" data-name="${esc(m.name)}">' in text


def test_activation_is_an_explicit_confirmed_button(tmp_path: Path) -> None:
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    assert 'data-testid="metrics.activate"' in text
    # The confirm has to say the rebind reaches every tab, not only the reader's own.
    assert "window.confirm" in text
    assert "every tab against this server follows" in text


def test_the_activate_button_gates_on_the_boot_capability(tmp_path: Path) -> None:
    # A reader who may not activate should learn it before pressing and confirming, so the button
    # reads the boot read's capability block (#1721) and renders disabled with the server's reason
    # (#1720). The block reports; the endpoint still refuses on its own.
    text = _fetch(tmp_path, "/serve.metrics.mjs")
    assert "unavailableReason('activate')" in text
    assert "disabled title=" in text


def test_a_refused_activation_names_the_right_it_needs(tmp_path: Path) -> None:
    # The second line, for a role that changed since boot: the server answers a role-gated activate
    # with a bare {"error": "forbidden"}, and the shared switch helper turns that into a sentence
    # rather than showing the transport's own word (#1720).
    text = _fetch(tmp_path, "/serve.core.mjs")
    assert "Only an admin can activate a project." in text
