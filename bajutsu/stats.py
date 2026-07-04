"""Aggregate run-stats dashboard (BE-0102) — the deterministic trend across many runs.

A read-only aggregation over the artifacts the runner already writes (`manifest.json` per run): it
turns a pile of runs into a picture — pass-rate over time, run/scenario durations, the scenarios and
steps that fail most, per-scenario flakiness, and run volume. Every figure is an exact count or
aggregation; there is no model and no verdict, and it is never part of the CI gate (a team may
*track* a number from it as informational, exactly as the coverage map allows).

It is the operational complement to the two analytical reports Bajutsu already ships — the coverage
map (BE-0050) answers *"what surface do we test?"* and the determinism audit (BE-0049) answers *"is a
given scenario reproducible?"*; this answers *"how is the whole suite doing over time?"* The
scenario-level series are keyed by the BE-0049 `(scenarioHash, name)` identity — a verdict that flips
at a constant fingerprint is true flakiness, while an edited scenario starts a fresh series — and the
flakiness classification is reused from the audit rather than re-derived.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from bajutsu.audit import longitudinal

# A run id opens with a UTC timestamp (`YYYYMMDD-HHMMSS`), so the day is a pure prefix parse; a run
# id that doesn't match (a custom label) simply has no day and buckets under "" (unknown).
_RUN_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})")


@dataclass(frozen=True)
class RunPoint:
    """One run as a point on the trend line — pass-rate-over-time and volume both read this."""

    run_id: str
    day: str  # YYYY-MM-DD parsed from run_id, "" when the id carries no timestamp
    ok: bool  # the run's top-level verdict
    passed: int  # scenarios that passed in this run
    total: int  # scenarios in this run
    duration_s: float  # the run's wall-clock, summed from its scenarios' durations
    backend: str  # the actuator that drove the run ("idb" / "fake" / …)


@dataclass(frozen=True)
class DayPoint:
    """A day's run pass-rate — the trend line at day granularity."""

    day: str  # YYYY-MM-DD, or "" for runs whose id carries no timestamp
    runs: int
    passed_runs: int
    pass_rate: float  # passed_runs / runs


@dataclass(frozen=True)
class ScenarioStat:
    """One scenario's aggregate at a fixed fingerprint — pass-rate, duration, and flakiness in one.

    Keyed by the BE-0049 `(scenarioHash, name)` identity, so a content edit starts a fresh series
    rather than corrupting the old one. Pass-rate and `classification` come from the audit's
    longitudinal view (reused, not re-derived); the durations are aggregated here.
    """

    scenario_hash: str
    name: str
    runs: int
    passed: int
    failed: int
    pass_rate: float
    avg_duration_s: float
    max_duration_s: float
    classification: str  # flaky | deterministic | unproven (BE-0049)


@dataclass(frozen=True)
class Hotspot:
    """A recurring failure ranked by frequency — a scenario, a step action, or an assertion kind."""

    key: str  # the scenario name / "scenario > action" / assertion kind that failed
    failures: int
    reason: str  # the most frequent failure reason among those failures ("" when none was recorded)


@dataclass(frozen=True)
class Stats:
    """The whole-suite trend: run totals, the time series, per-scenario aggregates, and hotspots."""

    runs: int
    passed_runs: int
    failed_runs: int
    pass_rate: float
    total_duration_s: float  # summed run wall-clock across the whole set
    by_run: list[RunPoint]  # chronological (oldest first) — pass-rate over time and volume
    by_day: list[DayPoint]  # chronological (oldest first)
    by_backend: dict[str, int]  # run count per actuator — the volume denominator
    scenarios: list[ScenarioStat]  # per (fingerprint, scenario): flaky first, then most-observed
    failing_scenarios: list[Hotspot]  # scenarios that fail most, by frequency
    failing_steps: list[Hotspot]  # step actions that fail most, by frequency
    failing_assertions: list[Hotspot]  # assertion kinds that fail most, by frequency
    scenarios_skipped: int  # runs with no scenarioHash — can't join a fingerprinted series


def aggregate_runs(manifests: Iterable[Mapping[str, object]]) -> Stats:
    """Aggregate a set of run manifests into the whole-suite trend (BE-0102).

    Pure and observational: it counts and aggregates the recorded outcomes only — no device, no
    network, no model, and it never re-runs an assertion or changes a verdict. An older manifest
    missing a newer field contributes what it has (a run with no `provenance.scenarioHash` counts
    toward the run-level trend but can't join a fingerprinted scenario series, so it lands in
    `scenarios_skipped`), mirroring the report's "re-present recorded outcomes, never re-run"
    discipline (BE-0068).

    Args:
        manifests: Parsed `manifest.json` mappings, in any order. Non-mapping entries and mappings
            without a `scenarios` list carry no run outcome and are ignored.

    Returns:
        The whole-suite `Stats`: run totals, the per-run and per-day time series, run volume by
        backend, the per-`(scenarioHash, name)` aggregates (flakiness reused from the BE-0049 audit),
        and the failure hotspots by scenario / step action / assertion kind.
    """
    runs = [m for m in manifests if isinstance(m, Mapping) and isinstance(m.get("scenarios"), list)]

    by_run = [_run_point(m) for m in runs]
    by_run.sort(key=lambda p: p.run_id)
    passed_runs = sum(1 for p in by_run if p.ok)

    # Reuse the BE-0049 audit for the flakiness classification, then fold in durations keyed by the
    # same (scenarioHash, name) identity. The two passes must stay grouped identically; if the
    # audit's key ever widens, `_scenario_durations` has to widen with it or the averages drift.
    hist = longitudinal(runs)
    durations = _scenario_durations(runs)
    scenarios = []
    for h in hist.histories:
        ds = durations.get((h.scenario_hash, h.name), [])
        scenarios.append(
            ScenarioStat(
                scenario_hash=h.scenario_hash,
                name=h.name,
                runs=h.runs,
                passed=h.passed,
                failed=h.failed,
                pass_rate=h.pass_rate,
                avg_duration_s=sum(ds) / len(ds) if ds else 0.0,
                max_duration_s=max(ds, default=0.0),
                classification=h.classification,
            )
        )

    return Stats(
        runs=len(by_run),
        passed_runs=passed_runs,
        failed_runs=len(by_run) - passed_runs,
        pass_rate=(passed_runs / len(by_run)) if by_run else 0.0,
        total_duration_s=sum(p.duration_s for p in by_run),
        by_run=by_run,
        by_day=_by_day(by_run),
        by_backend=_by_backend(by_run),
        scenarios=scenarios,
        failing_scenarios=_failing_scenarios(runs),
        failing_steps=_failing_steps(runs),
        failing_assertions=_failing_assertions(runs),
        scenarios_skipped=hist.skipped,
    )


def _run_point(m: Mapping[str, object]) -> RunPoint:
    """Reduce one manifest to its trend-line point — verdict, scenario tally, duration, backend."""
    scenarios = [s for s in _as_list(m.get("scenarios")) if isinstance(s, Mapping)]
    passed = sum(1 for s in scenarios if s.get("ok"))
    run_id = _as_str(m.get("runId"))
    raw_ok = m.get("ok")
    ok = raw_ok if isinstance(raw_ok, bool) else all(s.get("ok") for s in scenarios)
    return RunPoint(
        run_id=run_id,
        day=_day_of(run_id),
        ok=ok,
        passed=passed,
        total=len(scenarios),
        duration_s=sum(_as_float(s.get("duration_s")) for s in scenarios),
        backend=_as_str(m.get("backend")),
    )


def _day_of(run_id: str) -> str:
    """The `YYYY-MM-DD` a run id opens with, or "" when it carries no timestamp prefix."""
    match = _RUN_DAY.match(run_id)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def _by_day(points: list[RunPoint]) -> list[DayPoint]:
    """Roll the per-run points up into per-day pass-rate, oldest day first."""
    runs: Counter[str] = Counter()
    passed: Counter[str] = Counter()
    for p in points:
        runs[p.day] += 1
        if p.ok:
            passed[p.day] += 1
    return [
        DayPoint(
            day=day, runs=runs[day], passed_runs=passed[day], pass_rate=passed[day] / runs[day]
        )
        for day in sorted(runs)
    ]


def _by_backend(points: list[RunPoint]) -> dict[str, int]:
    """Run volume per actuator, the denominator the rates are read against."""
    volume: Counter[str] = Counter(p.backend for p in points)
    return dict(sorted(volume.items()))


def _scenario_durations(runs: list[Mapping[str, object]]) -> dict[tuple[str, str], list[float]]:
    """Per-(fingerprint, scenario) durations, keyed exactly as the BE-0049 longitudinal grouping.

    Only runs carrying a `provenance.scenarioHash` contribute, so the keys line up with the audit's
    histories (a run without a fingerprint is `scenarios_skipped` there and omitted here alike). A
    scenario whose `duration_s` is absent or non-numeric contributes no sample rather than a `0.0`,
    so a missing timing never drags the average down (it would read as a fast run that never happened).
    """
    durations: dict[tuple[str, str], list[float]] = {}
    for m in runs:
        prov = m.get("provenance")
        scenario_hash = prov.get("scenarioHash") if isinstance(prov, Mapping) else None
        if not isinstance(scenario_hash, str):
            continue
        for s in _as_list(m.get("scenarios")):
            if not isinstance(s, Mapping):
                continue
            name = s.get("scenario")
            duration = _opt_float(s.get("duration_s"))
            if isinstance(name, str) and name and duration is not None:
                durations.setdefault((scenario_hash, name), []).append(duration)
    return durations


def _failing_scenarios(runs: list[Mapping[str, object]]) -> list[Hotspot]:
    """Rank the scenarios that fail most, surfacing each one's most frequent failure reason."""
    reasons: dict[str, Counter[str]] = {}
    for m in runs:
        for s in _as_list(m.get("scenarios")):
            if isinstance(s, Mapping) and not s.get("ok"):
                name = s.get("scenario")
                if isinstance(name, str) and name:
                    reasons.setdefault(name, Counter())[_as_str(s.get("failure"))] += 1
    return _hotspots(reasons)


def _failing_steps(runs: list[Mapping[str, object]]) -> list[Hotspot]:
    """Rank the step actions that fail most (keyed `scenario > action`), with the recurring reason."""
    reasons: dict[str, Counter[str]] = {}
    for m in runs:
        for s in _as_list(m.get("scenarios")):
            if not isinstance(s, Mapping):
                continue
            name = _str_or(s.get("scenario"), "?")
            for step in _as_list(s.get("steps")):
                if isinstance(step, Mapping) and not step.get("ok"):
                    action = _str_or(step.get("action"), "?")
                    reasons.setdefault(f"{name} > {action}", Counter())[
                        _as_str(step.get("reason"))
                    ] += 1
    return _hotspots(reasons)


def _failing_assertions(runs: list[Mapping[str, object]]) -> list[Hotspot]:
    """Rank the assertion kinds that fail most, over both step-level and scenario-level checks."""
    reasons: dict[str, Counter[str]] = {}
    for m in runs:
        for s in _as_list(m.get("scenarios")):
            if not isinstance(s, Mapping):
                continue
            checks = list(_as_list(s.get("expect_results")))
            for step in _as_list(s.get("steps")):
                if isinstance(step, Mapping):
                    checks += _as_list(step.get("assertion_results"))
            for a in checks:
                if isinstance(a, Mapping) and not a.get("ok"):
                    kind = _str_or(a.get("kind"), "?")
                    reasons.setdefault(kind, Counter())[_as_str(a.get("reason"))] += 1
    return _hotspots(reasons)


def _hotspots(reasons: dict[str, Counter[str]]) -> list[Hotspot]:
    """Turn key→reason-tallies into ranked hotspots: most failures first, then key for stability."""
    hotspots = [
        Hotspot(key=key, failures=sum(tally.values()), reason=_top_reason(tally))
        for key, tally in reasons.items()
    ]
    hotspots.sort(key=lambda h: (-h.failures, h.key))
    return hotspots


def _top_reason(tally: Counter[str]) -> str:
    """The most frequent non-empty failure reason, or "" when none was recorded."""
    ranked = sorted(((n, r) for r, n in tally.items() if r), reverse=True)
    return ranked[0][1] if ranked else ""


# --- rendering: the same numbers as text (scriptable) and as one self-contained HTML page ---


def _slowest(scenarios: list[ScenarioStat]) -> list[ScenarioStat]:
    """Scenarios ranked by descending average duration — the shared slowest-first ordering."""
    return sorted(scenarios, key=lambda sc: -sc.avg_duration_s)


def _flaky(scenarios: list[ScenarioStat]) -> list[ScenarioStat]:
    """The scenarios the BE-0049 audit classified as flaky, in the aggregator's order."""
    return [sc for sc in scenarios if sc.classification == "flaky"]


def render(s: Stats) -> str:
    """Human-readable summary of the whole-suite trend — the scriptable, CI-publishable view.

    Leads with the headline pass-rate and run volume, then the slowest and flakiest scenarios and
    the top failure hotspots. Read-only and advisory, like every other stats output.
    """
    if not s.runs:
        return "no runs to aggregate"
    lines = [
        f"runs: {s.runs} ({s.passed_runs} passed, {s.failed_runs} failed, {s.pass_rate:.0%})",
        f"total duration: {s.total_duration_s:.1f}s",
    ]
    if s.by_backend:
        # Match the HTML view's "(unknown)" so a run with no recorded backend doesn't read as "=N".
        lines.append(
            "volume by backend: "
            + ", ".join(f"{b or '(unknown)'}={n}" for b, n in s.by_backend.items())
        )
    if s.by_day:
        lines.append("pass-rate by day:")
        lines.extend(
            f"  {d.day or '(no date)'}: {d.pass_rate:.0%} ({d.passed_runs}/{d.runs})"
            for d in s.by_day
        )
    slowest = _slowest(s.scenarios)[:5]
    if slowest:
        lines.append("slowest scenarios (avg):")
        lines.extend(
            f"  {sc.name}: {sc.avg_duration_s:.2f}s (max {sc.max_duration_s:.2f}s)"
            for sc in slowest
        )
    flaky = _flaky(s.scenarios)
    if flaky:
        lines.append("flaky scenarios:")
        lines.extend(
            f"  {sc.name}: {sc.passed}/{sc.runs} passed ({sc.pass_rate:.0%})" for sc in flaky
        )
    if s.failing_scenarios:
        lines.append("failure hotspots (scenarios):")
        lines.extend(
            f"  {h.key}: {h.failures}× {h.reason}".rstrip() for h in s.failing_scenarios[:5]
        )
    if s.scenarios_skipped:
        lines.append(f"skipped {s.scenarios_skipped} run(s) with no scenario fingerprint")
    return "\n".join(lines)


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    # autoescape so a stray "<" in a scenario name or reason can never inject markup into the page.
    return Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)


def render_html(s: Stats) -> str:
    """A self-contained HTML dashboard (inline CSS, minimal inline SVG, no JS, no external asset).

    The visual counterpart to `render`: the pass-rate trend, run volume, the slowest and flakiest
    scenarios, and the failure hotspots on one page that works opened straight from disk. Read-only
    and AI-free, mirroring the coverage report (BE-0050).
    """
    return (
        _env()
        .get_template("stats.html.j2")
        .render(stats=s, slowest=_slowest(s.scenarios), flaky=_flaky(s.scenarios))
    )


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _as_float(value: object) -> float:
    return _opt_float(value) or 0.0


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _str_or(value: object, default: str) -> str:
    return value if isinstance(value, str) else default
