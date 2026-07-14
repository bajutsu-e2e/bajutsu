"""Tests for the bajutsu serve static assets, run listing, and simulators (real ThreadingHTTPServer)."""

from __future__ import annotations

import json
import re
import urllib.error
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


def test_http_index_inlines_css_and_loads_js_as_modules(tmp_path: Path) -> None:
    """The index inlines the CSS/themes, but loads the JS as ES modules (BE-0247): it references the
    entry module via `<script type="module">` and preloads the rest, rather than inlining the code."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        status, body, ctype = _get(port, "/")
        text = body.decode("utf-8")
        assert status == 200 and ctype.startswith("text/html")
        assert '[data-theme="daylight"]' in text  # from serve.themes.css (still inlined)
        assert "--bg2" in text  # from serve.css (theme-aware inset color, still inlined)
        # The frontend loads as a module, not inlined: the entry <script type="module"> and a
        # modulepreload for every section module are present.
        assert '<script type="module" src="/serve.author.mjs">' in text
        for name in srv.handler._JS_MODULES:
            assert f'<link rel="modulepreload" href="/{name}">' in text
        # The JS is no longer inlined into the page — its code lives at the module routes now.
        assert "function showView" not in text
        assert "browseFs" not in text
    finally:
        server.shutdown()
        server.server_close()


def test_http_author_edits_round_trip_through_backend(tmp_path: Path) -> None:
    """The Author tab no longer builds scenario YAML in the browser (BE-0261): Apply / Accept POST to
    the round-trip endpoints and the serializer owns quoting. Structural fact only: the old
    client-side YAML builders are gone and the fetch calls to the new endpoints ship. This code lives
    in the serve.author.mjs module now (BE-0247), so assert against that route rather than the index."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        text = _get(port, "/serve.author.mjs")[1].decode("utf-8")
        # The hand-rolled YAML builders must be gone — quoting now lives in the backend serializer.
        assert "auSelectorYaml" not in text
        assert "enrichAssertionYaml" not in text
        # Apply / Accept round-trip through the new AI-free endpoints.
        assert "/api/scenario/apply-selector" in text
        assert "/api/scenario/enrich-apply" in text
    finally:
        server.shutdown()
        server.server_close()


def test_http_serves_each_js_module(tmp_path: Path) -> None:
    """BE-0247: each serve.*.mjs section is served as its own ES module at /serve.<name>.mjs (with a
    JavaScript MIME so the browser will execute it), not inlined. Assert every module route returns
    its unique marker as a real module (has an `export`), and that the three start handlers still
    route through the shared startJob skeleton in the modules that own them."""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        # One unique marker per module; each is served with a JavaScript MIME and is a real module.
        markers = {
            "serve.core.mjs": "function startJob",  # shared helpers
            "serve.panels.mjs": "function loadGenerated",  # Record/Replay/Triage
            "serve.crawl.mjs": "function openShot",  # crawl graph / lightbox
            "serve.metrics.mjs": "function renderMetrics",  # cross-project comparison
            "serve.author.mjs": "function initTiling",  # layout + Author + boot
        }
        assert set(markers) == set(srv.handler._JS_MODULES)  # every served module is covered here
        modules = {}
        for name, marker in markers.items():
            status, body, ctype = _get(port, f"/{name}")
            text = body.decode("utf-8")
            modules[name] = text
            assert status == 200, name
            assert "javascript" in ctype, (name, ctype)  # module scripts need a JS MIME to execute
            assert marker in text, name
            assert "export" in text, name  # it is an ES module, not a bare script
        # The three start buttons share startJob; each passes its own url + busy label. Record/Replay
        # live in panels, Crawl in crawl. Tolerate incidental whitespace / quote style.
        for url in ("/api/run", "/api/record"):
            assert re.search(rf"url:\s*['\"]{re.escape(url)}['\"]", modules["serve.panels.mjs"])
        assert re.search(r"url:\s*['\"]/api/crawl['\"]", modules["serve.crawl.mjs"])
        # startJob fails loudly on a network drop / non-JSON body / missing jobId (it fronts all three
        # start buttons) — the messages live in core. Quote-tolerant so a reformat doesn't break it.
        for msg in ("request failed", "no job started"):
            assert re.search(rf"['\"]{re.escape(msg)}['\"]", modules["serve.core.mjs"])
    finally:
        server.shutdown()
        server.server_close()


def test_http_js_module_route_is_traversal_safe(tmp_path: Path) -> None:
    """Only the exact bundled module names are served: a real module 200s, but an unknown name or a
    near-miss (wrong extension) 404s rather than reading anything off disk. (The open-before-auth
    exemption is covered separately, with a token set, in test_http_auth.py.)"""
    scn_dir, cfg, runs = project(tmp_path)
    server, port = _serve(
        srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    )
    try:
        assert _get(port, "/serve.core.mjs")[0] == 200
        for bad in ("/serve.nope.mjs", "/serve.core.js", "/serve.core.mjson"):
            try:
                status = _get(port, bad)[0]
            except urllib.error.HTTPError as e:  # urlopen raises on 4xx
                status = e.code
            assert status == 404, bad
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_carries_responsive_layout(tmp_path: Path) -> None:
    """The small-screen reflow (BE-0072): the CSS/markup ships inlined in the index, the JS guards
    ship in the modules (BE-0247).

    Structural facts only (the rules / markup / handlers are present), never a "looks good" check:
      * a phone-tier `@media (max-width: …)` breakpoint exists (serve.css has none on desktop),
      * the per-view segmented switcher markup + its active-pane CSS ship,
      * the narrow-tier guard that skips the persisted desktop tile/split layouts exists (author mod),
      * the crawl graph gained touch pan/pinch handlers (touchstart/touchmove, in the crawl module).
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
        assert ".viewswitch" in text  # the switcher's own styling ships (inline CSS)
        # The narrow guard lives in the author module now — don't apply desktop layouts on a phone.
        assert "NARROW_MQ" in _get(port, "/serve.author.mjs")[1].decode("utf-8")
        # Crawl-graph touch handlers live in the crawl module: pan + pinch reuse the zoom/pan math,
        # and touchcancel runs the same cleanup as touchend so a cancelled gesture can't leave the
        # graph stuck.
        crawl_js = _get(port, "/serve.crawl.mjs")[1].decode("utf-8")
        assert "touchstart" in crawl_js and "touchmove" in crawl_js and "touchcancel" in crawl_js
    finally:
        server.shutdown()
        server.server_close()


def test_http_index_carries_crawl_history_markup(tmp_path: Path) -> None:
    """The Crawl tab ships the history affordance (BE-0180): the Form/History sub-tabs, the list
    container, the read-only "past crawl" badge, and the crash/flow links strip ship as markup in the
    index; the JS wired to /api/crawl/runs ships in the crawl module. Structural facts only."""
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
        # The crawl module fetches the crawl-specific listing and reuses the existing graph render path.
        crawl_js = _get(port, "/serve.crawl.mjs")[1].decode("utf-8")
        assert "/api/crawl/runs" in crawl_js and "viewCrawlRun" in crawl_js
    finally:
        server.shutdown()
        server.server_close()


def test_serve_assets_present() -> None:
    """Guard against a template file going missing from the package — including every serve.*.mjs
    ES module (BE-0247), which handler.py serves at its own route."""
    for name in ("serve.html.j2", "serve.css", "serve.themes.css", *srv.handler._JS_MODULES):
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
