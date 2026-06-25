"""Load a finished run back into the report renderer (BE-0068).

The inverse of what the run writes: `manifest.json` (the versioned render model) reconstructs the
`RunResult`s, and `scenario.yaml` reconstructs the scenario plan (`definitions` / `sources`) the
report's Result tab merges with the outcomes. With those, the one renderer that bakes a report
during `run` can re-render a finished run offline — no device, no model, no re-run.

Reconstruction reads only the fields it knows: a missing field (an older `schemaVersion`) falls
back to its default, and an unknown newer field is ignored — so an older run still renders, with
newer-only views simply absent rather than failing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from bajutsu.assertions import AssertionResult, VisualEvidence
from bajutsu.evidence import Artifact
from bajutsu.orchestrator import AlertEvent, RunResult, StepOutcome
from bajutsu.report.html import html_report, scenario_render_inputs, write_html_and_junit
from bajutsu.scenario import load_scenario_file


# `manifest_dict` serializes via `asdict`; these reconstruct the inverse. `_kw` filters to the
# dataclass's fields, so a new *scalar* field flows through automatically and an older / newer
# manifest still loads; a new *nested* field (a list of sub-dataclasses) needs a line below, which
# the round-trip test (`test_round_trip_through_manifest_is_lossless`) catches by exercising each.
def _kw(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """The subset of `data` that names a field of dataclass `cls` (drops unknown / newer keys)."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _visual(d: dict[str, Any] | None) -> VisualEvidence | None:
    return VisualEvidence(**_kw(VisualEvidence, d)) if d else None


def _assertion(d: dict[str, Any]) -> AssertionResult:
    return AssertionResult(**{**_kw(AssertionResult, d), "visual": _visual(d.get("visual"))})


def _step(d: dict[str, Any]) -> StepOutcome:
    return StepOutcome(
        **{
            **_kw(StepOutcome, d),
            "assertion_results": [_assertion(a) for a in d.get("assertion_results") or []],
            "artifacts": [Artifact(**_kw(Artifact, a)) for a in d.get("artifacts") or []],
            "alerts": [AlertEvent(**_kw(AlertEvent, a)) for a in d.get("alerts") or []],
        }
    )


def _result(d: dict[str, Any]) -> RunResult:
    return RunResult(
        **{
            **_kw(RunResult, d),
            "steps": [_step(s) for s in d.get("steps") or []],
            "expect_results": [_assertion(a) for a in d.get("expect_results") or []],
            "artifacts": [Artifact(**_kw(Artifact, a)) for a in d.get("artifacts") or []],
            "expect_alerts": [
                AlertEvent(**_kw(AlertEvent, a)) for a in d.get("expect_alerts") or []
            ],
        }
    )


def results_from_manifest(data: dict[str, Any]) -> list[RunResult]:
    """Reconstruct the `RunResult`s from a parsed `manifest.json` (the inverse of `manifest_dict`)."""
    return [_result(s) for s in data.get("scenarios") or []]


@dataclass(frozen=True)
class RenderModel:
    """Everything the renderer needs, recovered from a run dir."""

    run_id: str
    results: list[RunResult]
    definitions: list[dict[str, Any]]
    sources: list[str]
    source_name: str | None
    description: str | None


def load_run(run_dir: Path) -> RenderModel:
    """Recover the render model from a finished run.

    Outcomes come from `manifest.json`, the scenario plan from `scenario.yaml`.

    Raises:
        OSError: If either file is missing or unreadable.
        ValueError: If either file is malformed.
    """
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    scenario_file = load_scenario_file((run_dir / "scenario.yaml").read_text(encoding="utf-8"))
    definitions, sources = scenario_render_inputs(scenario_file.scenarios)
    return RenderModel(
        run_id=str(manifest.get("runId") or run_dir.name),
        results=results_from_manifest(manifest),
        definitions=definitions,
        sources=sources,
        source_name=manifest.get("sourceName"),
        description=scenario_file.description,
    )


def rerender_html(run_dir: Path) -> str:
    """Re-render a finished run's `report.html` from its stored model, with the current template."""
    m = load_run(run_dir)
    return html_report(
        m.run_id, m.results, run_dir, m.definitions, m.sources, m.source_name, m.description
    )


def rebake(run_dir: Path) -> None:
    """Rewrite a finished run's `report.html` and `junit.xml` in place from its stored model.

    The manifest — the source of truth — is left untouched.
    """
    m = load_run(run_dir)
    write_html_and_junit(
        run_dir, m.run_id, m.results, m.definitions, m.sources, m.source_name, m.description
    )
