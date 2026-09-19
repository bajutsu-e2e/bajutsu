"""Jinja rendering.

Turn RunResults into a pure-data context and render the self-contained report.html (templates
live in bajutsu/templates/).
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.orchestrator import RunResult
from bajutsu.common.report.ctrf import ctrf_json
from bajutsu.common.report.format import _fmt_duration
from bajutsu.common.report.manifest import _matrix, _run_backend, junit_xml, manifest_dict
from bajutsu.common.report.panels import _scenario_data
from bajutsu.common.scenario import Scenario, declared_name, dump_scenarios, scenario_dict


@dataclass(frozen=True)
class ScenarioPlanSource:
    """Where one scenario's report plan should be read from.

    Recovered from its on-disk file before setup/component expansion can rewrite its `steps`.
    `text` is the scenario's own verbatim YAML (comments intact) — None when `run/cli.py` could
    not recover it (e.g. it fed the run some other way), in which case the report falls back to a
    structured re-dump with no comments, exactly as it always has. `step_lines` is each of the
    scenario's `steps` items' original 1-based line number in that file; empty once the caller's
    own expansion changed the step list's shape, since a wrong line is worse than none.
    """

    file_name: str
    text: str | None
    step_lines: list[int]


def scenario_render_inputs(
    scenarios: list[Scenario],
    plan_sources: Mapping[str, ScenarioPlanSource] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The renderer's per-scenario plan inputs, aligned with the scenarios.

    Returns `definitions` (structured) and `sources` (raw YAML) — a scenario's own verbatim text
    (comments intact) when `plan_sources` recovers one for it, else the structured re-dump this
    always fell back to. Shared by the run pipeline (initial bake) and the offline re-render
    (BE-0068); an offline re-render passes no `plan_sources` (the original file's text was never
    persisted), so it keeps re-dumping exactly as before.
    """
    plan_sources = plan_sources or {}

    def source(s: Scenario) -> str:
        plan = plan_sources.get(declared_name(s.name))
        return plan.text if (plan is not None and plan.text is not None) else dump_scenarios([s])

    return [scenario_dict(s) for s in scenarios], [source(s) for s in scenarios]


def scenario_source_meta(
    scenarios: list[Scenario],
    plan_sources: Mapping[str, ScenarioPlanSource] | None = None,
) -> tuple[list[str | None], list[list[int] | None]]:
    """Each scenario's originating file name and its steps' original line numbers.

    Aligned with `scenarios` (and so with `scenario_render_inputs`'s `sources`). Both are absent
    for a scenario `plan_sources` has nothing for; `step_lines` is also absent when the scenario's
    raw text itself was unavailable or its own line numbers were dropped as unreliable.
    """
    plan_sources = plan_sources or {}
    files: list[str | None] = []
    lines: list[list[int] | None] = []
    for s in scenarios:
        plan = plan_sources.get(declared_name(s.name))
        files.append(plan.file_name if plan is not None else None)
        if plan is not None and plan.text is not None and plan.step_lines:
            lines.append(plan.step_lines)
        else:
            lines.append(None)
    return files, lines


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


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
    source_files: list[str | None] | None = None,
    step_lines: list[list[int] | None] | None = None,
) -> str:
    """A self-contained interactive HTML report (inline CSS + JS, no external assets).

    When `run_dir` is given the captured logs/traces are embedded inline (so the report
    works opened directly from disk); otherwise only the structure renders.
    `definitions` (structured) and `sources` (raw YAML), both aligned with `results`,
    drive the merged Result tab and its Rich/YAML toggle. `source_files` names each scenario's
    own originating file (shown beside its name); `step_lines` gives each scenario's steps'
    original line numbers in that file (shown beside each executed step), when `scenario_source_meta`
    could recover them — both are omitted wherever their entry is None.
    """
    passed = sum(1 for r in results if r.ok)

    # `definitions` / `sources` carry one entry per scenario, but a cross-browser matrix run's
    # `results` is the per-engine passes concatenated (every engine runs the same scenarios in
    # order), so a result's plan is at `i % len(definitions)` — positional for a single-engine run,
    # cycling per engine for a matrix run (BE-0076).
    def _plan(seq: list[Any] | None, i: int) -> Any | None:
        return seq[i % len(seq)] if seq else None

    scenarios = [
        _scenario_data(
            r,
            run_dir,
            _plan(definitions, i),
            _plan(sources, i),
            _plan(source_files, i),
            _plan(step_lines, i),
        )
        for i, r in enumerate(results)
    ]
    # Ordered-unique device count. A multi-target scenario names one device per declared target
    # rather than one for itself (BE-0428), so those count too — otherwise a run every one of whose
    # scenarios was multi-target would report "0 devices".
    devices = dict.fromkeys(
        device
        for r in results
        for device in ([r.device] if r.device else [d.device for d in r.target_devices.values()])
        if device
    )
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


def _sink(run_dir: Path, writer: RunArtifactWriter | None) -> RunArtifactWriter:
    """The run's sink, or one built for `run_dir` when the caller has no secret values to mask.

    An offline re-render (BE-0068) reads a finished run and knows nothing of the values the original
    run bound, so it gets an inert redactor — the sink's pattern backstop still runs (BE-0331).
    """
    return writer if writer is not None else RunArtifactWriter(run_dir, Redactor(None))


def write_html_and_junit(
    run_dir: Path,
    run_id: str,
    results: list[RunResult],
    definitions: list[dict[str, Any]] | None = None,
    sources: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    provenance: dict[str, object] | None = None,
    writer: RunArtifactWriter | None = None,
    source_files: list[str | None] | None = None,
    step_lines: list[list[int] | None] | None = None,
) -> None:
    """Write (or rewrite) report.html + junit.xml + ctrf.json under run_dir, leaving manifest.json untouched.

    The renderable half of the report: the initial bake calls it after the manifest, and the
    offline re-render (BE-0068) calls it alone to refresh a finished run from its stored model.
    `provenance` (the manifest's run-identity stamp) only feeds the CTRF export's tool/environment
    fields; None omits them. `run_dir` is read (the report embeds the run's captured logs inline);
    every write goes through `writer`. `source_files` / `step_lines` are `html_report`'s own —
    see there.
    """
    sink = _sink(run_dir, writer)
    sink.write_text("junit.xml", junit_xml(results))
    sink.write_json("ctrf.json", ctrf_json(run_id, results, provenance=provenance))
    sink.write_text(
        "report.html",
        html_report(
            run_id,
            results,
            run_dir,
            definitions,
            sources,
            source_name,
            description,
            source_files,
            step_lines,
        ),
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
    writer: RunArtifactWriter | None = None,
    target: str | None = None,
    label: str | None = None,
    source_files: list[str | None] | None = None,
    step_lines: list[list[int] | None] | None = None,
) -> Path:
    """Write manifest.json (the versioned render model), junit.xml, and report.html under run_dir.

    `definitions` / `sources`, aligned with `results`, feed the report's merged Result tab and its
    Rich/YAML toggle. `provenance` is the run-identity stamp (BE-0049). `writer` is the run's sink.
    `target` / `label` are the target the run ran and its history label, stamped into the
    manifest (BE-0404 units 2 and 3). `source_files` / `step_lines` are `html_report`'s own —
    see there.

    Returns:
        The manifest.json path.
    """
    sink = _sink(run_dir, writer)
    manifest_path = sink.write_json(
        "manifest.json",
        manifest_dict(
            run_id,
            results,
            source_name=source_name,
            provenance=provenance,
            target=target,
            label=label,
        ),
    )
    write_html_and_junit(
        run_dir,
        run_id,
        results,
        definitions,
        sources,
        source_name,
        description,
        provenance,
        sink,
        source_files,
        step_lines,
    )
    return manifest_path
