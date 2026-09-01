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
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, cast

import yaml

from bajutsu.assertions import AssertionResult, VisualEvidence
from bajutsu.common.evidence import Artifact
from bajutsu.common.report.html import html_report, scenario_render_inputs, write_html_and_junit
from bajutsu.drivers.actuation import Actuation
from bajutsu.drivers.base import Frame, Point
from bajutsu.orchestrator import AlertEvent, RunResult, SkippedCapture, StepOutcome
from bajutsu.scenario import load_scenario_file

_logger = logging.getLogger("bajutsu.common.report.load")


# `manifest_dict` serializes via `asdict`; these reconstruct the inverse. `_kw` filters to the
# dataclass's fields, so a new *scalar* field flows through automatically and an older / newer
# manifest still loads; a new *nested* field (a list of sub-dataclasses) needs a line below, which
# the round-trip test (`test_round_trip_through_manifest_is_lossless`) catches by exercising each.
# `RunResult.wall_offset_s` is the one deliberate exception: `manifest_dict` excludes it (a
# same-process-only conversion constant with no meaning once persisted, BE-0348), so it always
# reconstructs at its default rather than round-tripping — don't read the guarantee above as
# covering it, and don't "fix" the round-trip test to assert otherwise.
def _kw(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """The subset of `data` that names a field of dataclass `cls` (drops unknown / newer keys)."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


def _visual(d: dict[str, Any] | None) -> VisualEvidence | None:
    return VisualEvidence(**_kw(VisualEvidence, d)) if d else None


def _assertion(d: dict[str, Any]) -> AssertionResult:
    return AssertionResult(**{**_kw(AssertionResult, d), "visual": _visual(d.get("visual"))})


def _numbers(v: Any, arity: int) -> tuple[float, ...] | None:
    """A JSON array back into the fixed-arity tuple an actuation record's geometry declares.

    JSON has no tuple, so `points` / `frame` arrive as lists and would otherwise compare unequal to
    what the run wrote — which the manifest round-trip test exists to catch. None for anything that is
    not exactly `arity` plain numbers, so the caller can drop the record rather than reconstruct a
    shape no writer produces.
    """
    if not isinstance(v, (list, tuple)) or len(v) != arity:
        return None
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v):
        return None
    return tuple(float(n) for n in v)


def _scalar(v: Any) -> float | None:
    """`v` as a plain float, or None when it is not exactly a JSON number.

    The scalar twin of `_numbers`: `duration_s` / `scale` / `radians` are read the same untrusting way
    geometry is, so a corrupt scalar degrades to "not recorded" rather than reaching `Actuation` as a
    string or bool the dataclass's type never allows.
    """
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _typed(v: Any, t: type) -> Any:
    """`v` unchanged if it is exactly type `t`, else None — the same degrade `_scalar` gives a number."""
    return v if isinstance(v, t) else None


def _dropped_count(v: Any) -> int:
    """A manifest's own drop counter as a plain non-negative int, or 0 when it is not one.

    This field exists to disclose a gap; a wrong-typed or negative value in it must not render as a
    nonsensical count (or crash the report the way a bad geometry value would) — that would make the
    disclosure mechanism the very failure it exists to catch.
    """
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _dropped_total(kw: dict[str, Any], key: str, extra: int) -> int:
    """The manifest's own disclosed drop count under `key`, plus a fresh drop the loader just counted."""
    return _dropped_count(kw.get(key)) + extra


def _actuation(d: dict[str, Any]) -> Actuation | None:
    """One actuation record, or None when the entry is malformed.

    Degradation is per *record*, not per field, and that is the whole point: a swipe whose second
    point is corrupt would otherwise reconstruct as a plausible one-point gesture, and a corrupt
    `frame` would become `None` — which in this schema *means* "the driver resolved no element", so a
    reader could not tell damage from a handle-based tap. Dropping the record instead keeps every
    surviving record trustworthy, and the caller counts what it dropped so the gap is disclosed.
    A missing required key drops the same way rather than raising, so one bad entry costs that entry
    and not the whole report render.
    """
    known = _kw(Actuation, d)
    if not all(isinstance(known.get(k), str) for k in ("gesture", "via", "unit")):
        return None
    raw_points = d.get("points") or []
    if not isinstance(raw_points, list):
        return None
    points = [_numbers(v, 2) for v in raw_points]
    if any(p is None for p in points):
        return None
    frame = None
    if d.get("frame") is not None:
        frame = _numbers(d["frame"], 4)
        if frame is None:
            return None
    # Explicit keyword arguments rather than a `**` unpack of `dict[str, Any]`: this is the one
    # function here that parses untrusted geometry, and unpacking would switch mypy off at exactly
    # that boundary — the runtime checks above (plus the `_typed`/`_scalar` guards below) are what
    # keep the whole record typed, not just the fields with dedicated checks.
    return Actuation(
        gesture=str(known["gesture"]),
        via=str(known["via"]),
        unit=str(known["unit"]),
        points=cast("tuple[Point, ...]", tuple(p for p in points if p is not None)),
        frame=cast("Frame | None", frame),
        target=_typed(known.get("target"), str),
        accepted=_typed(known.get("accepted"), bool),
        duration_s=_scalar(known.get("duration_s")),
        scale=_scalar(known.get("scale")),
        radians=_scalar(known.get("radians")),
        substitution=_typed(known.get("substitution"), str),
    )


def _actuations(entries: Any) -> tuple[list[Actuation], int]:
    """Every readable actuation record in `entries`, plus how many were malformed and dropped."""
    out, dropped = [], 0
    for e in entries or []:
        record = _actuation(e) if isinstance(e, dict) else None
        if record is None:
            dropped += 1
        else:
            out.append(record)
    if dropped:
        _logger.warning("dropped %d malformed actuation record(s) while loading a run", dropped)
    return out, dropped


def _step(d: dict[str, Any]) -> StepOutcome:
    actuations, dropped = _actuations(d.get("actuations"))
    kw = _kw(StepOutcome, d)
    return StepOutcome(
        **{
            **kw,
            "assertion_results": [_assertion(a) for a in d.get("assertion_results") or []],
            "artifacts": [Artifact(**_kw(Artifact, a)) for a in d.get("artifacts") or []],
            "alerts": [AlertEvent(**_kw(AlertEvent, a)) for a in d.get("alerts") or []],
            "actuations": actuations,
            # `dropped` is the loader's own casualty, on top of whatever the run itself already
            # disclosed (a driver-side truncation) in the same field — a run that also loads with a
            # damaged record must not read as more complete than either gap alone.
            "dropped_actuations": _dropped_total(kw, "dropped_actuations", dropped),
        }
    )


def _result(d: dict[str, Any]) -> RunResult:
    expect_actuations, expect_dropped = _actuations(d.get("expect_actuations"))
    kw = _kw(RunResult, d)
    return RunResult(
        **{
            **kw,
            "steps": [_step(s) for s in d.get("steps") or []],
            # The lifecycle phases' own outcomes (BE-0392), reconstructed through the same `_step`
            # as the scenario's own — a report re-rendered offline shows the Before / After blocks
            # a live run showed.
            "before_outcomes": [_step(s) for s in d.get("before_outcomes") or []],
            "after_outcomes": [_step(s) for s in d.get("after_outcomes") or []],
            "expect_results": [_assertion(a) for a in d.get("expect_results") or []],
            "artifacts": [Artifact(**_kw(Artifact, a)) for a in d.get("artifacts") or []],
            "expect_alerts": [
                AlertEvent(**_kw(AlertEvent, a)) for a in d.get("expect_alerts") or []
            ],
            "expect_actuations": expect_actuations,
            # The one other place an `Actuation` list lives on the manifest — a malformed entry here
            # must be disclosed the same way a step's own dropped actuation is, not silently swallowed.
            "dropped_expect_actuations": _dropped_total(
                kw, "dropped_expect_actuations", expect_dropped
            ),
            "skipped_captures": [
                SkippedCapture(**_kw(SkippedCapture, c)) for c in d.get("skipped_captures") or []
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
    # The manifest's run-identity stamp (BE-0049), replayed into the regenerated CTRF export
    # (BE-0161) so a re-render preserves the original run's tool version / commit; None if absent.
    provenance: dict[str, object] | None


def load_run(run_dir: Path) -> RenderModel:
    """Recover the render model from a finished run.

    Outcomes come from `manifest.json`, the scenario plan from `scenario.yaml`.

    Raises:
        OSError: If either file is missing or unreadable.
        ValueError: If either file is malformed — bad JSON/YAML, or a manifest whose shape the
            reconstruction can't read (so callers can catch one type for "can't load this run").
    """
    manifest_text = (run_dir / "manifest.json").read_text(encoding="utf-8")  # OSError if missing
    scenario_text = (run_dir / "scenario.yaml").read_text(encoding="utf-8")
    try:
        manifest = json.loads(manifest_text)
        scenario_file = load_scenario_file(scenario_text)
        definitions, sources = scenario_render_inputs(scenario_file.scenarios)
        return RenderModel(
            run_id=str(manifest.get("runId") or run_dir.name),
            results=results_from_manifest(manifest),
            definitions=definitions,
            sources=sources,
            source_name=manifest.get("sourceName"),
            description=scenario_file.description,
            provenance=manifest.get("provenance"),
        )
    except (yaml.YAMLError, TypeError, KeyError, AttributeError) as e:
        # json.JSONDecodeError and pydantic's ValidationError are already ValueErrors; normalize the
        # rest (a YAML parse error, a manifest missing fields the dataclasses require) to ValueError
        # so the loader honors its one documented malformed-input type.
        raise ValueError(f"malformed run model in {run_dir}: {e}") from e


def rerender_html(run_dir: Path) -> str:
    """Re-render a finished run's `report.html` from its stored model, with the current template."""
    m = load_run(run_dir)
    return html_report(
        m.run_id, m.results, run_dir, m.definitions, m.sources, m.source_name, m.description
    )


def rebake(run_dir: Path) -> None:
    """Rewrite a finished run's `report.html`, `junit.xml`, and `ctrf.json` in place from its stored model.

    The manifest — the source of truth — is left untouched.
    """
    m = load_run(run_dir)
    write_html_and_junit(
        run_dir,
        m.run_id,
        m.results,
        m.definitions,
        m.sources,
        m.source_name,
        m.description,
        m.provenance,
    )
