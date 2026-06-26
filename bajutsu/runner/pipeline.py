"""Run every scenario through a device pool and write the run's report artifacts."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from bajutsu import capability_preflight, idb_version
from bajutsu.assertions import SchemaContext, VisualContext
from bajutsu.backends import capabilities_for
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
from bajutsu.report import run_provenance, scenario_render_inputs, write_report
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
    schemas_dir: Path | None = None,
    actuator: str | None = None,
) -> list[RunResult]:
    """Run every scenario, each on a freshly leased device, and return one result per scenario.

    `lease(eff, scenario)` blocks until a device is free, launches the app, and returns a `Lease`
    bundling the live driver with that device's evidence sink / relaunch / control / network
    collector; `lease.release()` afterwards terminates the app and returns the device to the pool.
    A lease's collector, when present, has its exchanges cleared per scenario, exposed to `request`
    assertions, and written to `<sid>/network.json` (redacted with `secret_values`).

    Args:
        eff: The resolved target config (drives redaction, backend, launch).
        scenarios: The scenarios to run; results come back in this declaration order.
        lease: Leases a device and launches the app for one scenario (a single-device run is a pool
            of one).
        clock: Injectable time source for condition waits, so tests need no real sleeps. None uses
            the real clock.
        on_blocked: A single alert-guard handler, used by tests.
        on_blocked_for: Picks each scenario's alert-guard handler (honoring its `dismissAlerts`);
            takes precedence over `on_blocked`.
        run_dir: Where per-scenario artifacts (network.json, visual diffs) are written. None skips
            writing them.
        workers: Concurrent scenarios; >1 hands each worker its own device + per-device resources,
            so the loop keeps no shared mutable state.
        bindings: `secrets.<name>` → value substitutions applied to step inputs.
        secret_values: The raw secret values to redact from evidence.
        progress: Receives one-line progress messages (the web UI streams these). None is silent.
        baselines_dir: Baseline images for `visual` assertions. None disables visual comparison.
        schemas_dir: Directory the `responseSchema` assertions' schema files resolve against. None
            disables them.
        actuator: The selected actuator (e.g. `idb` / `playwright`); when given, each scenario is
            preflighted against its static capability set and failed up front if it needs a
            capability the actuator lacks (BE-0082). None skips the preflight (a lease driven
            directly in tests).

    Returns:
        One result per scenario, in the same order as `scenarios`.
    """
    redactor = Redactor(eff.redact, values=secret_values)
    # Preflight: a backend's capability set is static, so a scenario that needs a capability the
    # actuator lacks (e.g. pinch on idb) is failed here — before any device is leased — instead of
    # mid-run after partial device work (BE-0082). Skipped when no actuator is passed (tests that
    # drive a lease directly), so the gesture handler's own check still backstops it.
    caps = capabilities_for(actuator) if actuator is not None else None

    total = len(scenarios)

    def run_one(i: int, s: Scenario) -> RunResult:
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if progress is not None:
            progress(f"▶ scenario {i + 1}/{total}: {s.name}")
        if caps is not None and (reasons := capability_preflight.unsupported(s, caps)):
            if progress is not None:
                progress(f"✘ scenario {i + 1}/{total}: {s.name} (unsupported on {actuator})")
            return RunResult(
                scenario=s.name,
                ok=False,
                steps=[],
                backend=actuator or "",
                failure=f"unsupported on backend '{actuator}': {'; '.join(reasons)}",
            )
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
            sc = SchemaContext(schemas_dir=schemas_dir) if schemas_dir is not None else None
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
                schema_context=sc,
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
    schemas_dir: Path | None = None,
    actuator: str | None = None,
    config_source: dict[str, str] | None = None,
) -> tuple[list[RunResult], Path]:
    """Run the scenarios, then write the run's artifacts under `runs_dir/run_id`.

    Wraps `run_all` and persists the report: `manifest.json`, JUnit XML, and the executed
    `scenario.yaml` (so a run is re-runnable / reviewable). idb toolchain versions are recorded as
    provenance only when idb actually drove the run — never a pass/fail input (BE-0005).

    Beyond `run_all`'s arguments, `runs_dir` + `run_id` locate this run's artifact directory
    (`runs_dir/run_id`), `source_name` / `description` are recorded in the report, and
    `config_source` — the Git source the config came from (BE-0063), or None for a local config — is
    stamped into the manifest's provenance so a branch-based run states the exact commit it executed.

    Returns:
        The per-scenario results and the path to the written `manifest.json`.
    """
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
        schemas_dir=schemas_dir,
        actuator=actuator,
    )
    # The merged Result tab renders each scenario as a structured view (definitions) with a toggle
    # to the raw YAML (sources). The same helper feeds the offline re-render, so the two match.
    definitions, sources = scenario_render_inputs(scenarios)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Keep the executed scenario alongside its results (re-runnable / reviewable).
    scenario_yaml = dump_scenario_file(scenarios, description)
    (run_dir / "scenario.yaml").write_text(scenario_yaml, encoding="utf-8")
    # Record the idb versions this run was driven against, but only when idb actually drove it —
    # provenance for the artifact set, never a pass/fail input (BE-0005). Non-idb runs probe nothing.
    # idb-by-name is fine while idb is the only backend with a toolchain version; when a second
    # backend needs versions, generalize this to a `Driver.provenance()` hook instead of a name test.
    idb_versions = idb_version.probe() if any(r.backend == "idb" for r in results) else None
    # Stamp the run's identity (scenario fingerprint + tool/git version) so accumulated runs can be
    # grouped to tell true flakiness from an edited scenario (BE-0049); pure metadata, never a verdict.
    provenance = run_provenance(
        scenario_yaml, git_revision=_git_revision(), config_source=config_source
    )
    manifest = write_report(
        run_dir,
        run_id,
        results,
        definitions,
        sources,
        source_name=source_name,
        description=description,
        idb_versions=idb_versions,
        provenance=provenance,
    )
    # Final safety net: scrub any literal secret value that reached a run-level artifact
    # (e.g. an assertion's expected/actual text in the manifest / HTML). The scenario
    # definitions already hold tokens, not values, so this only catches result text.
    _scrub_secret_values(run_dir, secret_values)
    return results, manifest


def _git_revision() -> str | None:
    """The current git commit, or None when the run isn't inside a git checkout.

    Best-effort run provenance (BE-0049): any failure — not a repo, git absent — yields None so the
    stamp simply omits the revision rather than aborting the run.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 — git resolved on PATH; any failure → None below
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    # A shimmed/aliased `git` could exit 0 with blank stdout; treat that as "unknown", not an empty stamp.
    return out.stdout.strip() or None


def _scrub_secret_values(run_dir: Path, secret_values: list[str] | None) -> None:
    if not secret_values:
        return
    scrub = Redactor(None, values=secret_values)
    for name in ("manifest.json", "junit.xml", "report.html", "scenario.yaml"):
        path = run_dir / name
        if path.exists():
            path.write_text(scrub.redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")
