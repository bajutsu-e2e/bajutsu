"""serve renders report.html on view from the stored model, not the baked file (BE-0068).

Upgrading serve should refresh every report it shows with no per-run re-bake — so the served
report.html is rendered fresh from `manifest.json` + `scenario.yaml`, and a stale baked file on disk
is never returned.
"""

from __future__ import annotations

from pathlib import Path

from _shared import _get, _serve

from bajutsu import serve as srv
from bajutsu.assertions import AssertionResult
from bajutsu.orchestrator import RunResult, StepOutcome
from bajutsu.report import rerender_html, scenario_render_inputs, write_report
from bajutsu.scenario import dump_scenario_file, load_scenarios

SCENARIO = "- name: smoke\n  steps:\n    - tap: { id: home.start }\n  expect:\n    - exists: { id: home.title }\n"


def _bake(run_dir: Path) -> None:
    scenarios = load_scenarios(SCENARIO)
    definitions, sources = scenario_render_inputs(scenarios)
    results = [
        RunResult(
            scenario="smoke",
            ok=True,
            steps=[StepOutcome(index=0, action="tap home.start")],
            expect_results=[AssertionResult(ok=True, kind="exists", detail="home.title")],
            backend="xcuitest",
        )
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.yaml").write_text(dump_scenario_file(scenarios), encoding="utf-8")
    write_report(run_dir, run_dir.name, results, definitions, sources, source_name="smoke.yaml")


def test_render_report_returns_fresh_html_not_the_baked_file(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _bake(runs / "r1")
    fresh = rerender_html(runs / "r1")
    (runs / "r1" / "report.html").write_text("STALE", encoding="utf-8")  # an old/edited bake
    art = srv.LocalArtifactStore(runs).render_report("r1")
    assert art is not None
    assert art.content_type == "text/html"
    assert art.body == fresh.encode("utf-8")  # rendered from the model, not the stale file


def test_render_report_is_none_without_a_manifest(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    (runs / "bare").mkdir(parents=True)  # a dir with no manifest.json
    assert srv.LocalArtifactStore(runs).render_report("bare") is None
    assert srv.LocalArtifactStore(runs).render_report("../escape") is None  # confinement


def test_render_report_falls_back_when_the_model_cant_load(tmp_path: Path) -> None:
    # A run with a manifest but a corrupt scenario.yaml can't be re-rendered — render_report returns
    # None (so the caller serves the baked file) rather than raising out of the request.
    runs = tmp_path / "runs"
    _bake(runs / "r1")
    (runs / "r1" / "scenario.yaml").write_text("{ not: valid: yaml ::", encoding="utf-8")
    assert srv.LocalArtifactStore(runs).render_report("r1") is None


def test_http_report_html_is_rendered_on_view(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _bake(runs / "r1")
    (runs / "r1" / "report.html").write_text("STALE", encoding="utf-8")
    server, port = _serve(srv.ServeState(runs_dir=runs, cwd=tmp_path))
    try:
        status, body, ctype = _get(port, "/runs/r1/report.html")
        assert status == 200 and "text/html" in ctype
        assert b"STALE" not in body  # the stale baked file is not served
        assert b"smoke" in body  # the report rendered from the stored model
    finally:
        server.shutdown()
        server.server_close()
