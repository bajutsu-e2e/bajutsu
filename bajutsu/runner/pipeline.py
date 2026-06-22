"""Run every scenario through a device pool and write the run's report artifacts."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bajutsu.assertions import VisualContext
from bajutsu.config import Effective
from bajutsu.evidence import Artifact
from bajutsu.network import NetworkExchange
from bajutsu.orchestrator import (
    BlockedHandler,
    Clock,
    ProgressFn,
    RunResult,
    run_scenario,
    scenario_slug,
)
from bajutsu.redaction import Redactor
from bajutsu.report import scenario_render_inputs, write_report
from bajutsu.runner.types import LeaseFn, OnBlockedFor, _no_net
from bajutsu.scenario import Scenario, dump_scenario_file


def _write_network(
    timed: list[tuple[NetworkExchange, float]],
    scenario_start: float,
    run_dir: Path,
    sid: str,
    redactor: Redactor,
) -> Artifact | None:
    """Write a scenario's observed exchanges to <sid>/network.json (redacted).

    Each exchange gets a `startedAt` offset (seconds from the scenario's start, the same
    frame as a step's `started_at`) so the report can place it on the timeline: the
    receive time is ≈ completion, so the start is `received - scenario_start - duration`.
    """
    if not timed:
        return None
    data: list[dict[str, Any]] = []
    for ex, received in timed:
        d = ex.model_dump(by_alias=True, exclude_none=True)
        d["startedAt"] = round(
            max(0.0, received - scenario_start - (ex.duration_ms or 0.0) / 1000.0), 3
        )
        data.append(redactor.redact_exchange(d))
    text = json.dumps(data, ensure_ascii=False, indent=2)
    out = run_dir / sid / "network.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return Artifact(f"{sid}/network.json", "network", "collector")


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    on_blocked_for: OnBlockedFor | None = None,
    run_dir: Path | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
) -> list[RunResult]:
    """Run every scenario, each on a freshly leased device.

    `lease(eff, scenario)` blocks until a device is free, launches the app, and returns a
    Lease bundling the live driver with that device's evidence sink / relaunch / control /
    network collector. After the scenario finishes, `lease.release()` terminates the app and
    returns the device to the pool. When the lease carries a collector, its exchanges are
    cleared per scenario, exposed to `request` assertions, and written to <sid>/network.json
    (redacted with `secret_values`).

    `on_blocked_for`, when given, picks each scenario's alert-guard handler (honoring its
    `dismissAlerts`); it takes precedence over the single `on_blocked` (used by tests).

    With `workers > 1` scenarios run concurrently (results stay in declaration order). The
    pool hands each worker its own device and per-device resources, so the run loop has no
    shared mutable state.
    """
    redactor = Redactor(eff.redact, values=secret_values)

    total = len(scenarios)

    def run_one(i: int, s: Scenario) -> RunResult:
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if progress is not None:
            progress(f"▶ scenario {i + 1}/{total}: {s.name}")
        lz = lease(eff, s)
        handler = on_blocked_for(s) if on_blocked_for is not None else on_blocked
        try:
            if lz.collector is not None:
                lz.collector.clear()
            # t0 after launch, so exchange offsets share the step timeline's origin.
            scenario_start = time.monotonic()
            # Build visual context for scenario-level visual assertions (expect).
            vc: VisualContext | None = None
            if baselines_dir is not None and run_dir is not None:
                vc = VisualContext(
                    screenshot_path=run_dir / sid / "visual-actual.png",
                    baselines_dir=baselines_dir,
                    diff_dir=run_dir / sid,
                    run_dir=run_dir,
                )
            result = run_scenario(
                lz.driver,
                s,
                clock,
                sink=lz.sink,
                on_blocked=handler,
                scenario_id=sid,
                network=(lz.collector.snapshot if lz.collector is not None else _no_net),
                relaunch=lz.relaunch,
                bindings=bindings,
                control=lz.control,
                progress=progress,
                visual_context=vc,
            )
            result.device = lz.udid  # attribute the scenario to the device that ran it
            result.device_name = lz.device_name  # for the report's Environment tab
            result.device_runtime = lz.device_runtime
            if lz.collector is not None and run_dir is not None:
                art = _write_network(
                    lz.collector.snapshot_timed(), scenario_start, run_dir, sid, redactor
                )
                if art is not None:
                    result.artifacts.append(art)
            if progress is not None:
                mark = "✔" if result.ok else "✘"
                progress(f"{mark} scenario {i + 1}/{total}: {s.name} ({result.duration_s:.1f}s)")
            return result
        finally:
            lz.release()

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda pair: run_one(*pair), list(enumerate(scenarios))))
    return [run_one(i, s) for i, s in enumerate(scenarios)]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    on_blocked: BlockedHandler | None = None,
    on_blocked_for: OnBlockedFor | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
) -> tuple[list[RunResult], Path]:
    """Run scenarios and write manifest.json + JUnit + scenario.yaml under runs_dir/run_id.

    When `baselines_dir` is given, `visual` assertions compare each scenario's end-state
    screenshot against a baseline image in that directory (see run_all)."""
    run_dir = runs_dir / run_id
    results = run_all(
        eff,
        scenarios,
        lease,
        clock,
        on_blocked=on_blocked,
        on_blocked_for=on_blocked_for,
        run_dir=run_dir,
        workers=workers,
        bindings=bindings,
        secret_values=secret_values,
        progress=progress,
        baselines_dir=baselines_dir,
    )
    # The merged Result tab renders each scenario as a structured view (definitions) with a toggle
    # to the raw YAML (sources). The same helper feeds the offline re-render, so the two match.
    definitions, sources = scenario_render_inputs(scenarios)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Keep the executed scenario alongside its results (re-runnable / reviewable).
    (run_dir / "scenario.yaml").write_text(
        dump_scenario_file(scenarios, description), encoding="utf-8"
    )
    manifest = write_report(
        run_dir, run_id, results, definitions, sources, source_name, description
    )
    # Final safety net: scrub any literal secret value that reached a run-level artifact
    # (e.g. an assertion's expected/actual text in the manifest / HTML). The scenario
    # definitions already hold tokens, not values, so this only catches result text.
    _scrub_secret_values(run_dir, secret_values)
    return results, manifest


def _scrub_secret_values(run_dir: Path, secret_values: list[str] | None) -> None:
    if not secret_values:
        return
    scrub = Redactor(None, values=secret_values)
    for name in ("manifest.json", "junit.xml", "report.html", "scenario.yaml"):
        path = run_dir / name
        if path.exists():
            path.write_text(scrub.redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")
