"""HTTP-level tests for the codegen route (BE-0137).

The route surfaces `bajutsu codegen` in the serve Web UI: given a scenario in a target's
scenarios dir, it returns the generated native test source (XCUITest, Playwright, or UI Automator)
plus a filename. It is a structural mapping — deterministic, no device, no AI, no verdict."""

from __future__ import annotations

from pathlib import Path

from _shared import SCENARIO, _post, _serve

from bajutsu import serve as srv


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A scenarios dir shared by an iOS target (`demo`), a web target (`site`), and an Android one.

    `site` carries a `baseUrl`, so it is a web target and Playwright codegen applies; `demo`
    is iOS and XCUITest applies; `droid` carries a `package` and the adb backend, so UI Automator
    applies. All point at the same scenarios dir so one `smoke.yaml` exercises any emit path."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(SCENARIO, encoding="utf-8")
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(
        "defaults: { backend: [ios] }\ntargets:\n"
        f"  demo: {{ bundleId: com.example.demo, scenarios: {scn_dir} }}\n"
        f"  site: {{ baseUrl: 'http://127.0.0.1:8787/', backend: [web], scenarios: {scn_dir} }}\n"
        f"  droid: {{ package: com.example.droid, backend: [adb], scenarios: {scn_dir} }}\n",
        encoding="utf-8",
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    return scn_dir, cfg, runs


def test_http_codegen_xcuitest(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "demo", "scenario": "smoke.yaml", "emit": "xcuitest"}
        )
        assert status == 200
        assert body["filename"] == "SmokeUITests.swift"
        assert "import XCTest" in body["code"]
        assert "final class SmokeUITests: XCTestCase {" in body["code"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_playwright(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "site", "scenario": "smoke.yaml", "emit": "playwright"}
        )
        assert status == 200
        assert body["filename"] == "smoke.spec.ts"
        assert "import { test, expect } from '@playwright/test';" in body["code"]
        assert "http://127.0.0.1:8787/" in body["code"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_uiautomator(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/codegen",
            {"target": "droid", "scenario": "smoke.yaml", "emit": "uiautomator"},
        )
        assert status == 200
        assert body["filename"] == "SmokeUITest.kt"
        assert "import androidx.test.uiautomator.By" in body["code"]
        assert 'private const val PACKAGE = "com.example.droid"' in body["code"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_uiautomator_needs_android_target(tmp_path: Path) -> None:
    # A web target has no package, so UI Automator codegen is not available — the route says so
    # rather than emitting a broken test (mirroring the Playwright/web guard above).
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port,
            "/api/codegen",
            {"target": "site", "scenario": "smoke.yaml", "emit": "uiautomator"},
        )
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_playwright_needs_web_target(tmp_path: Path) -> None:
    # An iOS target has no baseUrl, so Playwright codegen is not available — the route says so
    # rather than emitting a broken test (honest about limits, mirroring `--emit`).
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "demo", "scenario": "smoke.yaml", "emit": "playwright"}
        )
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_rejects_unknown_emit(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "demo", "scenario": "smoke.yaml", "emit": "espresso"}
        )
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_unknown_target(tmp_path: Path) -> None:
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "nope", "scenario": "smoke.yaml", "emit": "xcuitest"}
        )
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_malformed_scenario_is_a_clean_400(tmp_path: Path) -> None:
    # A stored scenario that no longer parses must surface a clean 400, not crash the request.
    scn_dir, cfg, runs = _project(tmp_path)
    (scn_dir / "broken.yaml").write_text("- name: x\n  steps: not-a-list\n", encoding="utf-8")
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "demo", "scenario": "broken.yaml", "emit": "xcuitest"}
        )
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()


def test_http_codegen_missing_scenario(tmp_path: Path) -> None:
    # A scenario name with no matching file in the target's scenarios dir resolves to nothing -> 404.
    scn_dir, cfg, runs = _project(tmp_path)
    state = srv.ServeState(scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path)
    server, port = _serve(state)
    try:
        status, body = _post(
            port, "/api/codegen", {"target": "demo", "scenario": "absent.yaml", "emit": "xcuitest"}
        )
        assert status == 404
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()
