"""Tests for the bajutsu serve static assets, run listing, and simulators (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from _shared import (
    _get,
    _get_json,
    _serve,
    project,
    write_run,
)

from bajutsu import serve as srv


def test_http_lists_and_index(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert b"bajutsu" in _get(port, "/")[1]
        assert _get_json(port, "/api/scenarios?target=demo")[0]["names"] == ["alpha", "beta"]
        assert [a["name"] for a in _get_json(port, "/api/targets")] == ["demo", "other"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_inlines_assets(tmp_path: Path) -> None:
    """The index serves one self-contained doc with the CSS/JS/themes inlined."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, body, ctype = _get(port, "/")
        text = body.decode("utf-8")
        assert status == 200 and ctype.startswith("text/html")
        assert '[data-theme="daylight"]' in text  # from serve.themes.css
        assert "--bg2" in text  # from serve.css (theme-aware inset color)
        assert "function showView" in text  # from serve.core.js
        assert "function applyTheme" in text  # the dark / light toggle logic
        assert "browseFs" in text  # config-browser JS survives the split
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_author_edits_round_trip_through_backend(tmp_path: Path) -> None:
    """The Author tab no longer builds scenario YAML in the browser (BE-0261): Apply / Accept POST to
    the round-trip endpoints and the serializer owns quoting. Structural fact only: the old
    client-side YAML builders are gone and the fetch calls to the new endpoints ship."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = _get(port, "/")[1].decode("utf-8")
        # The hand-rolled YAML builders must be gone — quoting now lives in the backend serializer.
        assert "auSelectorYaml" not in text
        assert "enrichAssertionYaml" not in text
        # Apply / Accept round-trip through the new AI-free endpoints.
        assert "/api/scenario/apply-selector" in text
        assert "/api/scenario/enrich-apply" in text
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_concatenates_all_js_sections(tmp_path: Path) -> None:
    """BE-0202: serve.js is split into serve.*.js section files that handler.py concatenates, in a
    fixed order, into the one inlined <script>. Assert a marker unique to each section survives, so a
    dropped file (or a broken _JS_ASSETS order) fails here, and that the three start handlers route
    through the shared startJob skeleton rather than the old copy-pasted bodies."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = _get(port, "/")[1].decode("utf-8")
        # One marker per section file — all four must be present in the concatenated script.
        assert "function startJob" in text  # serve.core.js (shared helpers)
        assert "function loadGenerated" in text  # serve.panels.js (Record/Replay/Triage)
        assert "function openShot" in text  # serve.crawl.js (crawl graph / lightbox)
        assert "function initTiling" in text  # serve.author.js (layout + Author + boot)
        # The three start buttons now share startJob; each passes its own url + busy label.
        # Tolerate incidental whitespace / quote style so a reformat doesn't break the routing check.
        for url in ("/api/run", "/api/record", "/api/crawl"):
            assert re.search(rf"url:\s*['\"]{re.escape(url)}['\"]", text)
        # startJob fails loudly on a network drop / non-JSON body / missing jobId — it must reset the
        # button rather than leave it stuck spinning (it fronts all three start buttons). Quote-tolerant
        # so a reformat of the section files doesn't break the check.
        for msg in ("request failed", "no job started"):
            assert re.search(rf"['\"]{re.escape(msg)}['\"]", text)
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_carries_responsive_layout(tmp_path: Path) -> None:
    """The served doc carries the small-screen reflow (BE-0072), all inlined — no asset pipeline.

    Structural facts only (the rules / markup / handlers are present), never a "looks good" check:
      * a phone-tier `@media (max-width: …)` breakpoint exists (serve.css has none on desktop),
      * the per-view segmented switcher markup + its active-pane CSS ship,
      * the narrow-tier guard that skips the persisted desktop tile/split layouts exists (serve.*.js),
      * the crawl graph gained touch pan/pinch handlers (touchstart/touchmove).
    """
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = _get(port, "/")[1].decode("utf-8")
        # The phone breakpoint — the load-bearing decision the desktop CSS never had.
        assert "@media (max-width:640px)" in text
        # The per-view switcher: markup (one container per view) + the active-pane CSS class.
        # Four views since BE-0098 folded Capture + Editor into one Author tab: record, replay,
        # crawl, author.
        assert text.count('class="viewswitch"') == 4
        for label in ("Form", "Log", "Report", "Progress", "Output", "Graph", "Plan", "Console"):
            assert f">{label}</button>" in text
        assert ".viewswitch" in text  # the switcher's own styling ships
        # The narrow guard in the serve UI JS: don't apply the persisted desktop layouts on a phone.
        assert "NARROW_MQ" in text
        # Crawl-graph touch: pan + pinch reuse the existing zoom/pan math.
        assert "touchstart" in text and "touchmove" in text
        # A cancelled touch (gesture takeover, context switch) must reset the pan/pinch state, so it
        # can't leave the graph stuck — touchcancel runs the same cleanup as touchend.
        assert "touchcancel" in text
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_carries_crawl_history_markup(tmp_path: Path) -> None:
    """The Crawl tab ships the history affordance (BE-0180): the Form/History sub-tabs, the list
    container, the read-only "past crawl" badge, the crash/flow links strip, and the JS wired to
    /api/crawl/runs. Structural facts only — never a "looks good" check."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = _get(port, "/")[1].decode("utf-8")
        assert 'id="crawl-histtab"' in text and 'data-tab="crawlhistory"' in text
        assert 'id="crawl-history"' in text  # the list container
        assert 'id="crawl-pastbadge"' in text  # read-only framing badge
        assert 'id="crawl-artifacts"' in text  # crash/flow links strip
        # The JS fetches the crawl-specific listing and reuses the existing graph render path.
        assert "/api/crawl/runs" in text and "viewCrawlRun" in text
    finally:
        server.shutdown()
        server.server_close()


def test_serve_assets_present() -> None:
    """Guard against a template file going missing from the package — including every serve.*.js
    section file (BE-0202), which handler.py concatenates into the inlined <script>."""
    for name in ("serve.html.j2", "serve.css", "serve.themes.css", *srv.handler._JS_ASSETS):
        assert srv.handler._asset(name).strip()  # non-empty


def test_http_runs_history(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    write_run(runs, "20260610-1", ok=True, scenarios=[("alpha", True)])
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        hist = _get_json(port, "/api/runs")
        assert len(hist) == 1 and hist[0]["id"] == "20260610-1" and hist[0]["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_http_crawl_runs_lists_screenmaps(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    # A crawl run: screenmap.json + one crash and two flow scenario files.
    d = runs / "20260610-1"
    (d / "crashes").mkdir(parents=True)
    (d / "flows").mkdir(parents=True)
    (d / "screenmap.json").write_text(
        json.dumps({"nodes": [{}, {}], "edges": [{}], "crashes": [{}]}), encoding="utf-8"
    )
    (d / "crashes" / "crash-001.yaml").write_text("- name: c\n", encoding="utf-8")
    (d / "flows" / "flow-001.yaml").write_text("- name: f\n", encoding="utf-8")
    (d / "flows" / "flow-002.yaml").write_text("- name: f\n", encoding="utf-8")
    write_run(runs, "20260610-0", ok=True, scenarios=[("alpha", True)])  # a replay run, not a crawl
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        crawls = _get_json(port, "/api/crawl/runs")
        assert [r["id"] for r in crawls] == ["20260610-1"]  # only the screenmap run
        assert crawls[0]["screens"] == 2 and crawls[0]["transitions"] == 1
        assert crawls[0]["crashes"] == 1
        assert crawls[0]["crashFiles"] == ["crash-001.yaml"]
        assert crawls[0]["flowFiles"] == ["flow-001.yaml", "flow-002.yaml"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_simulators(tmp_path: Path) -> None:
    scn_dir, cfg, runs = project(tmp_path)
    payload = json.dumps(
        {
            "devices": {
                "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                    {"udid": "U1", "name": "iPhone 17 Pro", "state": "Booted", "isAvailable": True}
                ]
            }
        }
    )
    state = srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        cwd=tmp_path,
        simctl=lambda args, e=None: payload,
    )
    server, port = _serve(state)
    try:
        sims = _get_json(port, "/api/simulators")
        assert sims == [
            {"udid": "U1", "name": "iPhone 17 Pro", "runtime": "iOS 26.5", "booted": True}
        ]
    finally:
        server.shutdown()
        server.server_close()
