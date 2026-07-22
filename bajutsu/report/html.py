"""Jinja rendering.

Turn RunResults into a pure-data context and render the self-contained report.html (templates
live in bajutsu/templates/).
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from bajutsu.orchestrator import RunResult
from bajutsu.report.ctrf import ctrf_json
from bajutsu.report.format import _fmt_duration
from bajutsu.report.manifest import _matrix, _run_backend, junit_xml, manifest_dict
from bajutsu.report.panels import _scenario_data
from bajutsu.scenario import Scenario, dump_scenarios, scenario_dict


def scenario_render_inputs(
    scenarios: list[Scenario],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The renderer's per-scenario plan inputs, aligned with the scenarios.

    Returns `definitions` (structured) and `sources` (raw YAML). Shared by the run pipeline
    (initial bake) and the offline re-render (BE-0068), so both feed the renderer identical inputs.
    """
    return [scenario_dict(s) for s in scenarios], [dump_scenarios([s]) for s in scenarios]


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _matrix_view(results: list[RunResult]) -> dict[str, Any] | None:
    """The engine x scenario grid for the report, or None for a single-engine run (BE-0076).

    Reuses `_matrix` (the manifest's aggregation) and reshapes its `cells` into ordered rows of
    booleans aligned with the engine columns, so the template renders a plain table with no JS.
    """
    matrix = _matrix(results)
    if matrix is None:
        return None
    engines: list[str] = matrix["engines"]  # type: ignore[assignment]
    scenarios: list[str] = matrix["scenarios"]  # type: ignore[assignment]
    cells: dict[str, dict[str, dict[str, Any]]] = matrix["cells"]  # type: ignore[assignment]
    rows = [{"scenario": s, "cells": [cells[s].get(e) for e in engines]} for s in scenarios]
    return {"engines": engines, "rows": rows}


# --- Jinja rendering ---


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


@functools.lru_cache(maxsize=2)
def _asset(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def html_report(
    run_id: str,
    results: list[RunResult],
    run_dir: Path | None = None,
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the report
    works opened directly from disk); otherwise only the structure renders.
    `definitions` (structured) and `sources` (raw YAML), both aligned with `results`,
    drive the merged Result tab and its Rich/YAML toggle.
    """
    passed = sum(1 for r in results if r.ok)

    # `definitions` / `sources` carry one entry per scenario, but a cross-browser matrix run's
    # `results` is the per-engine passes concatenated (every engine runs the same scenarios in
    # order), so a result's plan is at `i % len(definitions)` — positional for a single-engine run,
    # cycling per engine for a matrix run (BE-0076).
    def _plan(seq: list[Any] | None, i: int) -> Any | None:
        return seq[i % len(seq)] if seq else None

    scenarios = [
        _scenario_data(r, run_dir, _plan(definitions, i), _plan(sources, i))
        for i, r in enumerate(results)
    ]
    devices = dict.fromkeys(r.device for r in results if r.device)  # ordered-unique device count
    total_duration = _fmt_duration(sum(r.duration_s for r in results))
    return (
        _env()
        .get_template("report.html.j2")
        .render(
            run_id=run_id,
            passed=passed,
            failed=len(results) - passed,
            overall=passed == len(results),
            backend=_run_backend(results),
            device_count=len(devices),
            total_duration=total_duration,
            css=_asset("report.css"),
            js=_asset("report.js"),
            scenarios=scenarios,
            matrix=_matrix_view(results),
            source_name=source_name,
            description=description,
        )
    )


def write_html_and_junit(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    provenance: dict[str, object] | None = None,
) -> None:
    """Write (or rewrite) report.html + junit.xml + ctrf.json under run_dir, leaving manifest.json untouched.

    The renderable half of the report: the initial bake calls it after the manifest, and the
    offline re-render (BE-0068) calls it alone to refresh a finished run from its stored model.
    `provenance` (the manifest's run-identity stamp) only feeds the CTRF export's tool/environment
    fields; None omits them.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "junit.xml").write_text(junit_xml(results), encoding="utf-8")
    (run_dir / "ctrf.json").write_text(
        json.dumps(ctrf_json(run_id, results, provenance=provenance), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "report.html").write_text(
        html_report(run_id, results, run_dir, definitions, sources, source_name, description),
        encoding="utf-8",
    )


def write_report(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    provenance: dict[str, object] | None = None,
) -> Path:
    """Write manifest.json (the versioned render model), junit.xml, and report.html under run_dir.

    `definitions` / `sources`, aligned with `results`, feed the report's merged Result tab and its
    Rich/YAML toggle. `provenance` is the run-identity stamp (BE-0049).

    Returns:
        The manifest.json path.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest_dict(
                run_id,
                results,
                source_name=source_name,
                provenance=provenance,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_html_and_junit(
        run_dir, run_id, results, definitions, sources, source_name, description, provenance
    )
    return manifest_path
