"""Cross-run flakiness score over the serve DB run history (BE-0220, Half 1).

Mines the run records a hosted or self-hosted `serve` accumulates and ranks scenarios by how much
their verdict flips at a constant content fingerprint. It reuses `audit --history`'s exact
classification (`bajutsu.audit.classify_stability`) so the DB-backed surface and the file-backed
`audit --history` label a scenario identically.

Determinism-first, like BE-0049: this only *reports* flakiness read from recorded verdicts. It
computes no pass/fail, retries nothing, and gates nothing — nothing here is on the `run` / CI
verdict path.

The DB `Run` record carries one run-level verdict (`ok`) and one provenance stamp
(`scenario_hash`) per run, so the grouping key here is the `scenario_hash` and the metric is the
run-level verdict flip — the coarser DB counterpart to `audit --history`'s per-scenario grouping.
For the common single-scenario run the two coincide.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from bajutsu.audit import classify_stability
from bajutsu.run_id import parse_run_id_timestamp
from bajutsu.serve.server.db import RunRecord


def _as_utc(dt: datetime) -> datetime:
    """Return dt as a UTC-aware datetime. SQLite (the gate backend) hands back naive datetimes for
    ``DateTime(timezone=True)`` columns; assume UTC when tzinfo is absent, mirroring db._as_utc."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class FlakyScenario:
    """One scenario's cross-run stability at a fixed fingerprint — the unit of the ranked surface."""

    scenario_hash: str  # the runs' shared `provenance.scenarioHash`, the grouping key
    name: str  # a representative scenario name from the runs' summaries (for display / linking)
    runs: int  # runs observed at this fingerprint inside the window
    passed: int  # runs that passed
    failed: int  # runs that failed
    flip_rate: float  # 2 * min(passed, failed) / runs — 0 when consistent, 1 at a 50/50 split
    classification: str  # flaky | deterministic | unproven (see `audit.classify_stability`)
    representative_pass_run_id: str | None  # newest passing run, for linking to its evidence
    representative_fail_run_id: str | None  # newest failing run, for linking to its evidence


@dataclass(frozen=True)
class FlakinessReport:
    """The suite ranked by flakiness — flaky scenarios first, then by descending flip rate."""

    scenarios: list[FlakyScenario]  # flaky first, then flip_rate desc, then run count desc
    skipped: int  # runs dropped for lacking a fingerprint or a recorded verdict — ungroupable


def _scenario_name(record: RunRecord) -> str:
    """A representative scenario name from a run's summary, or empty when it lists none."""
    summary = record.summary
    scenarios = summary.get("scenarios")
    if isinstance(scenarios, list) and scenarios and scenarios[0]:
        return str(scenarios[0])
    return ""


def _recency_key(record: RunRecord) -> tuple[bool, datetime | None]:
    """Sort key ordering newest-first while keeping records with no timestamp last (never comparing
    a datetime against None). Normalises to UTC-aware to avoid naive/aware comparison errors from
    SQLite-backed runs."""
    return (
        record.created_at is not None,
        _as_utc(record.created_at) if record.created_at is not None else None,
    )


def rank_flakiness(
    records: Iterable[RunRecord],
    *,
    window_runs: int | None = None,
    since: datetime | None = None,
) -> FlakinessReport:
    """Rank scenarios by cross-run flakiness over the DB run history.

    Groups runs by `scenario_hash`, reuses `audit --history`'s classification, and orders the flaky
    scenarios to the top. A run with no `scenario_hash` (pre-provenance) or no recorded verdict
    (`ok is None`, e.g. still queued or errored before a verdict) can't contribute and is counted in
    `skipped`, mirroring `audit --history`.

    Args:
        records: Run records to mine, in any order.
        window_runs: Keep only each scenario's newest this-many runs (by `created_at`); unbounded
            when None. Must be a positive integer when set — zero or negative is caller-invalid
            and raises ValueError.
        since: Drop runs created before this instant (and any run with no `created_at`) when set.

    Returns:
        The ranked scenarios (flaky first, then descending flip rate, then run count) plus the count
        of runs skipped for lacking a fingerprint or verdict.
    """
    if window_runs is not None and window_runs <= 0:
        raise ValueError(f"window_runs must be a positive integer, got {window_runs!r}")
    groups: dict[str, list[RunRecord]] = {}
    skipped = 0
    for record in records:
        if since is not None and (
            record.created_at is None or _as_utc(record.created_at) < _as_utc(since)
        ):
            continue  # outside the window — not counted, not skipped
        if not isinstance(record.scenario_hash, str) or record.ok is None:
            skipped += 1
            continue
        groups.setdefault(record.scenario_hash, []).append(record)

    scenarios = [
        _score(scenario_hash, _window(runs, window_runs)) for scenario_hash, runs in groups.items()
    ]
    # Flaky first (the findings to act on), then flakiest by flip rate, then the most-observed.
    scenarios.sort(
        key=lambda s: (s.classification != "flaky", -s.flip_rate, -s.runs, s.scenario_hash)
    )
    return FlakinessReport(scenarios=scenarios, skipped=skipped)


def _window(runs: list[RunRecord], window_runs: int | None) -> list[RunRecord]:
    """A scenario's runs newest-first, trimmed to the window when one is set."""
    ordered = sorted(runs, key=_recency_key, reverse=True)
    return ordered[:window_runs] if window_runs is not None else ordered


def _score(scenario_hash: str, runs: list[RunRecord]) -> FlakyScenario:
    """Tally one scenario's windowed runs into a classified, ranked entry."""
    passed = sum(1 for r in runs if r.ok)
    failed = len(runs) - passed
    total = len(runs)
    representatives = _representatives(runs)
    return FlakyScenario(
        scenario_hash=scenario_hash,
        name=next((name for r in runs if (name := _scenario_name(r))), ""),
        runs=total,
        passed=passed,
        failed=failed,
        flip_rate=2 * min(passed, failed) / total if total else 0.0,
        classification=classify_stability(passed, total),
        representative_pass_run_id=representatives[0],
        representative_fail_run_id=representatives[1],
    )


def _representatives(runs: list[RunRecord]) -> tuple[str | None, str | None]:
    """The newest passing and newest failing run ids, for linking a row to both evidences.

    Assumes *runs* is already newest-first — true for the only caller (_score, via _window).
    """
    passing = next((r.id for r in runs if r.ok), None)
    failing = next((r.id for r in runs if not r.ok), None)
    return passing, failing


def records_from_manifests(manifests: Iterable[Mapping[str, object]]) -> list[RunRecord]:
    """Build the minimal `RunRecord`s the flakiness score reads from parsed run manifests.

    The provenance stamp lives on the DB record (BE-0220 prerequisite), so the DB surface groups
    straight from it; the file-backed `--history` CLI form and a local (no-database) serve have only
    the runs' `manifest.json` to read. This reduces each manifest to the same shape — the run-level
    verdict, the `provenance.scenarioHash` grouping key, a representative scenario name, and the run
    id (as both the identity and, parsed, the `created_at` used for windowing) — so all three inputs
    feed `rank_flakiness` identically. Read-only: it carries over recorded verdicts, deciding none.

    Args:
        manifests: Parsed `manifest.json` mappings, in any order. A manifest with no run-level `ok`
            or no `provenance.scenarioHash` still yields a record — `rank_flakiness` counts it in
            `skipped`, exactly as it does a pre-provenance DB row.
    """
    return [_record_from_manifest(m) for m in manifests]


def _record_from_manifest(manifest: Mapping[str, object]) -> RunRecord:
    """Reduce one parsed manifest to the fields the flakiness score reads (see records_from_manifests)."""
    run_id = manifest.get("runId")
    run_id = run_id if isinstance(run_id, str) else ""
    raw_ok = manifest.get("ok")
    prov = manifest.get("provenance")
    scenario_hash = prov.get("scenarioHash") if isinstance(prov, Mapping) else None
    scenarios = manifest.get("scenarios")
    names = [
        name
        for s in (scenarios if isinstance(scenarios, list) else [])
        if isinstance(s, Mapping) and (name := s.get("scenario"))
    ]
    return RunRecord(
        id=run_id,
        org_id="",
        status="",
        ok=raw_ok if isinstance(raw_ok, bool) else None,
        created_at=parse_run_id_timestamp(run_id),
        summary={"scenarios": names},
        scenario_hash=scenario_hash if isinstance(scenario_hash, str) else None,
    )


def render(report: FlakinessReport) -> str:
    """The CLI text form of the ranked report — flaky scenarios first, one block each.

    The `--json` form emits the report verbatim (`dataclasses.asdict`); this is the human-readable
    counterpart, mirroring `audit --history`'s `render_longitudinal`. Read-only, no verdict.
    """
    if not report.scenarios:
        body = ["no runs with a scenario fingerprint to rank"]
    else:
        body = [_render_scenario(s) for s in report.scenarios]
    if report.skipped:
        body.append(f"skipped {report.skipped} run(s) with no fingerprint or verdict")
    return "\n".join(body)


def _render_scenario(s: FlakyScenario) -> str:
    """One scenario's text block: its class, verdict tally, flip rate, and representative run ids."""
    head = f"{s.name or s.scenario_hash}: {s.classification} "
    head += f"({s.runs} runs · {s.passed} passed / {s.failed} failed · flip {s.flip_rate:.0%})"
    evidence = " · ".join(
        part
        for part in (
            f"pass {s.representative_pass_run_id}" if s.representative_pass_run_id else "",
            f"fail {s.representative_fail_run_id}" if s.representative_fail_run_id else "",
        )
        if part
    )
    return f"{head}\n  {evidence}" if evidence else head


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    # autoescape so a stray "<" in a scenario name can never inject markup into the page.
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


def render_html(report: FlakinessReport) -> str:
    """The serve panel: a self-contained HTML page ranking the suite by flakiness (BE-0220, Half 1).

    Inline CSS, no JS, no external asset — like the stats dashboard (BE-0102), so it renders inside
    the serve tab's shadow root and opens straight from disk. Each row links to the representative
    passing and failing runs' evidence under the existing `/runs/<id>/...` mount. Read-only and
    AI-free: it displays the ranking, computing and gating nothing.
    """
    return _env().get_template("flakiness.html.j2").render(report=report)
