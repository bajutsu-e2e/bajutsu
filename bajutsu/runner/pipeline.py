"""Run every scenario through a device pool and write the run's report artifacts."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bajutsu import capability_preflight
from bajutsu.artifact_perms import make_run_dir, restrict_file
from bajutsu.assertions import (
    AssertionResult,
    EvalContext,
    GoldenContext,
    SchemaContext,
    VisualContext,
    VisualEvidence,
)
from bajutsu.backends import capabilities_for_run
from bajutsu.config import Effective
from bajutsu.evidence import Artifact
from bajutsu.evidence.network import NetworkExchange, _no_transitions
from bajutsu.evidence.redaction import Redactor
from bajutsu.orchestrator import (
    AlertGuardConfig,
    Clock,
    MailboxReader,
    ProgressFn,
    RunResult,
    run_scenario,
    scenario_slug,
)
from bajutsu.orchestrator.types import _no_network
from bajutsu.report import git_revision, run_provenance, scenario_render_inputs, write_report
from bajutsu.runner.mailbox import build_mailbox_reader
from bajutsu.runner.types import AlertGuardFor, LeaseFn
from bajutsu.scenario import Scenario, dump_scenario_file, redact_totp_secrets

_logger = logging.getLogger(__name__)


def _write_network(
    timed: list[tuple[NetworkExchange, float]],
    scenario_start: float,
    run_dir: Path,
    sid: str,
    redactor: Redactor,
    provider: str = "collector",
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
    # network.json can carry request/response bodies and headers — owner-only, umask-independent (BE-0131).
    restrict_file(out)
    return Artifact(f"{sid}/network.json", "network", provider)


@dataclass(frozen=True)
class _ScenarioRunner:
    """One run's shared context, applied to each scenario in turn (BE-0172).

    Promoted from the ``run_one`` closure that lived inside ``run_all``: the values every scenario
    needs — the resolved config, the lease factory, the per-run redactor / mailbox / capability set,
    and the output knobs — are explicit read-only fields instead of captured free variables. This
    makes ``run_one`` legible and unit-testable in isolation, and makes explicit exactly which state
    each worker touches: the runner is frozen (no attribute rebinding) and holds no per-scenario
    mutable state — each ``run_one`` keeps its scenario state local — so it is shared across
    ``ThreadPoolExecutor`` workers as-is, precisely as the closure's captured state was.
    """

    eff: Effective
    lease: LeaseFn
    redactor: Redactor
    mailbox: MailboxReader | None
    caps: frozenset[str] | None
    total: int
    clock: Clock | None = None
    alert_guard: AlertGuardConfig | None = None
    alert_guard_for: AlertGuardFor | None = None
    run_dir: Path | None = None
    bindings: Mapping[str, str] | None = None
    progress: ProgressFn | None = None
    baselines_dir: Path | None = None
    schemas_dir: Path | None = None
    actuator: str | None = None
    golden_context: GoldenContext | None = None
    # The run's resolved udid spec (the provider's `udid_spec`): a WebDriver URL routes the run to the
    # live XCUITest environment, so the preflight below narrows to that transport's set — keyed on the
    # same signal `environment_for` routes on (BE-0238). "booted" (the default) is never a URL.
    udid_spec: str = "booted"
    # Per-scenario actuator resolver (BE-0240): when set, each scenario's actuator (and thus its
    # capability set for the preflight below) is resolved from the scenario itself, rather than one
    # fixed `actuator` for the whole run. The pool's `lease()` resolves the *same* pure function, so
    # the actuator preflighted here is the one the lease builds. None keeps the fixed-`actuator` path
    # (the cross-browser matrix, tests driving a lease directly).
    resolve_actuator: Callable[[Scenario], str] | None = None

    def run_one(self, i: int, s: Scenario) -> RunResult:
        """Run one scenario on a freshly leased device and return its result.

        Args:
            i: The scenario's zero-based index, used for its ordered `NN-slug` evidence dir.
            s: The scenario to run.
        """
        sid = f"{i:02d}-{scenario_slug(s.name)}"
        if self.progress is not None:
            self.progress(f"▶ scenario {i + 1}/{self.total}: {s.name}")
        # Resolve this scenario's actuator and capability set. With a per-scenario resolver (BE-0240)
        # the cheapest actuator the scenario can run on is chosen here; with none, the run's fixed
        # `actuator`/`caps` are used (today's path). Either way the preflight fails a scenario the
        # actuator can't run *before* any device is leased (BE-0082 fail-fast).
        actuator = self.actuator
        caps = self.caps
        if self.resolve_actuator is not None:
            try:
                actuator = self.resolve_actuator(s)
            except RuntimeError as exc:
                # No iOS actuator is even available (e.g. no xcodebuild and idb absent): a clean
                # per-scenario failure, not a crash that aborts the whole run.
                if self.progress is not None:
                    self.progress(f"✘ scenario {i + 1}/{self.total}: {s.name} ({exc})")
                return RunResult(
                    scenario=s.name, ok=False, steps=[], backend="", sid=sid, failure=str(exc)
                )
            caps = capabilities_for_run(actuator, self.eff, self.udid_spec)
        if caps is not None and (reasons := capability_preflight.unsupported(s, caps)):
            if self.progress is not None:
                self.progress(
                    f"✘ scenario {i + 1}/{self.total}: {s.name} (unsupported on {actuator})"
                )
            return RunResult(
                scenario=s.name,
                ok=False,
                steps=[],
                backend=actuator or "",
                sid=sid,
                failure=f"unsupported on backend '{actuator}': {'; '.join(reasons)}",
            )
        lz = self.lease(self.eff, s)
        handler = self.alert_guard_for(s) if self.alert_guard_for is not None else self.alert_guard
        try:
            if lz.collector is not None:
                lz.collector.clear()
            # t0 after launch, so exchange offsets share the step timeline's origin.
            scenario_start = time.monotonic()
            # Build visual context for scenario-level visual assertions (expect).
            vc: VisualContext | None = None
            if self.baselines_dir is not None and self.run_dir is not None:
                vc = VisualContext(
                    screenshot_path=self.run_dir / sid / "visual-actual.png",
                    baselines_dir=self.baselines_dir,
                    diff_dir=self.run_dir / sid,
                    run_dir=self.run_dir,
                    default_compare=self.eff.visual_compare,
                )
            sc = (
                SchemaContext(schemas_dir=self.schemas_dir)
                if self.schemas_dir is not None
                else None
            )
            # Best-effort device screen bounds for golden frame sanity (BE-0006):
            # a query() failure here must not block non-golden scenarios.
            gc_with_screen = self.golden_context
            if self.golden_context is not None and self.golden_context.screen is None:
                try:
                    from bajutsu.elements import screen_size_from_elements

                    sw, sh = screen_size_from_elements(lz.driver.query())
                    gc_with_screen = GoldenContext(
                        goldens_dir=self.golden_context.goldens_dir, screen=(0.0, 0.0, sw, sh)
                    )
                except Exception as exc:  # best-effort; _eval_golden falls back
                    _logger.debug(
                        "screen-bounds probe for golden framing failed: %s", exc, exc_info=True
                    )
            result = run_scenario(
                lz.driver,
                s,
                self.clock,
                sink=lz.sink,
                alert_guard=handler,
                scenario_id=sid,
                network=(lz.collector.snapshot if lz.collector is not None else _no_network),
                relaunch=lz.relaunch,
                bindings=self.bindings,
                control=lz.control,
                progress=self.progress,
                ctx=EvalContext(visual=vc, schema=sc, golden=gc_with_screen),
                mailbox=self.mailbox,
                webview_bridge=lz.webview_bridge,
                transitions=(
                    lz.collector.transitions_snapshot_timed
                    if lz.collector is not None
                    else _no_transitions
                ),
                # Config-level interrupts first, then the scenario's own (BE-0314): an app-wide
                # interstitial handler composes with a per-scenario addition, the config-then-scenario
                # order the dismissAlerts default already follows.
                interrupts=[*self.eff.run_defaults.interrupts, *s.interrupts],
            )
            result.sid = sid  # the evidence-dir slug, so the matrix links to the real dir (BE-0076)
            result.device = lz.udid  # attribute the scenario to the device that ran it
            result.device_name = lz.device_name  # for the report's Environment tab
            result.device_runtime = lz.device_runtime
            result.skipped_captures = list(lz.skipped_captures)  # disclose evidence gaps (BE-0020)
            if lz.collector is not None and self.run_dir is not None:
                art = _write_network(
                    lz.collector.snapshot_timed(),
                    scenario_start,
                    self.run_dir,
                    sid,
                    self.redactor,
                    provider=lz.collector_provider,
                )
                if art is not None:
                    result.artifacts.append(art)
            if self.progress is not None:
                mark = "✔" if result.ok else "✘"
                self.progress(
                    f"{mark} scenario {i + 1}/{self.total}: {s.name} ({result.duration_s:.1f}s)"
                )
            return result
        finally:
            lz.release()


def run_all(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    clock: Clock | None = None,
    alert_guard: AlertGuardConfig | None = None,
    alert_guard_for: AlertGuardFor | None = None,
    run_dir: Path | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
    schemas_dir: Path | None = None,
    actuator: str | None = None,
    resolve_actuator: Callable[[Scenario], str] | None = None,
    golden_context: GoldenContext | None = None,
    lease_udid_spec: str = "booted",
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
        alert_guard: A single alert-guard handler, used by tests.
        alert_guard_for: Picks each scenario's alert-guard handler (honoring its `dismissAlerts`);
            takes precedence over `alert_guard`.
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
        actuator: The single selected actuator (e.g. `xcuitest` / `playwright`); when given, each scenario
            is preflighted against its static capability set and failed up front if it needs a
            capability the actuator lacks (BE-0082). None skips the fixed preflight (a lease driven
            directly in tests, or when `resolve_actuator` chooses per scenario instead).
        resolve_actuator: Per-scenario actuator resolver (BE-0240); when given, each scenario's
            actuator — and thus the capability set it is preflighted against — is resolved from the
            scenario's own steps (cheapest sufficient), instead of the one fixed `actuator`. Mutually
            exclusive with `actuator` (passing both raises): the CLI's single-engine path and `audit`
            pass this, the cross-browser matrix passes `actuator`.
        golden_context: Goldens directory for `golden` assertions (BE-0006). None disables them.
        lease_udid_spec: The run's resolved udid spec (the provider's `udid_spec`). A WebDriver URL
            routes the run to the live XCUITest environment, so the preflight narrows to that
            transport's set (BE-0238) — the same `is_webdriver_endpoint` signal `environment_for`
            routes on. "booted" (the default) is never a URL, so the local path is unchanged.

    Returns:
        One result per scenario, in the same order as `scenarios`.
    """
    # `actuator` (one fixed actuator) and `resolve_actuator` (per-scenario, BE-0240) are two ways to
    # answer the same question; passing both is a caller bug. Fail loudly rather than silently letting
    # the resolver win and discarding the fixed actuator/caps (prime directive 2).
    if actuator is not None and resolve_actuator is not None:
        raise ValueError("pass either actuator or resolve_actuator to run_all, not both")
    redactor = Redactor(eff.redact, values=secret_values)
    # One mailbox reader for the whole run (it's per-target, not per-device): the `email` step polls
    # it, with ${secrets.*} in the url/headers resolved from the same secret bindings (BE-0046).
    mailbox = build_mailbox_reader(eff.mailbox, bindings or {})
    # Preflight: a backend's capability set is (near-)static, so a scenario that needs a capability
    # the actuator lacks (e.g. simctl device control on a real iOS device — BE-0238) is failed
    # here — before any device is leased — instead of mid-run after partial device work
    # (BE-0082). `capabilities_for_run` applies the run's one config-driven narrowing (real-device
    # XCUITest). Skipped when no actuator is passed (tests that drive a lease directly), so the
    # gesture handler's own check still backstops it.
    caps = capabilities_for_run(actuator, eff, lease_udid_spec) if actuator is not None else None

    runner = _ScenarioRunner(
        eff=eff,
        lease=lease,
        redactor=redactor,
        mailbox=mailbox,
        caps=caps,
        total=len(scenarios),
        clock=clock,
        alert_guard=alert_guard,
        alert_guard_for=alert_guard_for,
        run_dir=run_dir,
        bindings=bindings,
        progress=progress,
        baselines_dir=baselines_dir,
        schemas_dir=schemas_dir,
        actuator=actuator,
        resolve_actuator=resolve_actuator,
        golden_context=golden_context,
        udid_spec=lease_udid_spec,
    )
    if workers > 1:
        # >1 hands each worker its own device + per-device resources; the runner is frozen and
        # holds no per-scenario mutable state, so sharing it across workers adds none.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda pair: runner.run_one(*pair), list(enumerate(scenarios))))
    return [runner.run_one(i, s) for i, s in enumerate(scenarios)]


def run_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    lease: LeaseFn,
    runs_dir: Path,
    run_id: str,
    clock: Clock | None = None,
    alert_guard: AlertGuardConfig | None = None,
    alert_guard_for: AlertGuardFor | None = None,
    workers: int = 1,
    bindings: Mapping[str, str] | None = None,
    secret_values: list[str] | None = None,
    source_name: str | None = None,
    description: str | None = None,
    progress: ProgressFn | None = None,
    baselines_dir: Path | None = None,
    schemas_dir: Path | None = None,
    actuator: str | None = None,
    resolve_actuator: Callable[[Scenario], str] | None = None,
    config_source: dict[str, str] | None = None,
    exec_provenance: dict[str, str | None] | None = None,
    golden_context: GoldenContext | None = None,
    lease_udid_spec: str = "booted",
) -> tuple[list[RunResult], Path]:
    """Run the scenarios, then write the run's artifacts under `runs_dir/run_id`.

    Wraps `run_all` and persists the report: `manifest.json`, JUnit XML, and the executed
    `scenario.yaml` (so a run is re-runnable / reviewable).

    Beyond `run_all`'s arguments, `runs_dir` + `run_id` locate this run's artifact directory
    (`runs_dir/run_id`), `source_name` / `description` are recorded in the report, and
    `config_source` — the Git source the config came from (BE-0063), or None for a local config — is
    stamped into the manifest's provenance so a branch-based run states the exact commit it executed.

    Returns:
        The per-scenario results and the path to the written `manifest.json`.
    """
    run_dir = runs_dir / run_id
    # Create the run dir owner-only up front, before any scenario write creates it world-readable
    # under the ambient umask; everything underneath then inherits a non-world-readable parent (BE-0131).
    make_run_dir(run_dir)
    results = run_all(
        eff,
        scenarios,
        lease,
        clock,
        alert_guard=alert_guard,
        alert_guard_for=alert_guard_for,
        run_dir=run_dir,
        workers=workers,
        bindings=bindings,
        secret_values=secret_values,
        progress=progress,
        baselines_dir=baselines_dir,
        schemas_dir=schemas_dir,
        actuator=actuator,
        resolve_actuator=resolve_actuator,
        golden_context=golden_context,
        lease_udid_spec=lease_udid_spec,
    )
    manifest = _assemble_report(
        scenarios,
        results,
        run_dir,
        run_id,
        description=description,
        source_name=source_name,
        secret_values=secret_values,
        config_source=config_source,
        exec_provenance=exec_provenance,
    )
    return results, manifest


def run_matrix_and_report(
    eff: Effective,
    scenarios: list[Scenario],
    engines: list[str],
    run_pass: Callable[[str, Path], list[RunResult]],
    runs_dir: Path,
    run_id: str,
    *,
    source_name: str | None = None,
    description: str | None = None,
    secret_values: list[str] | None = None,
    config_source: dict[str, str] | None = None,
    exec_provenance: dict[str, str | None] | None = None,
) -> tuple[list[RunResult], Path]:
    """Run the scenarios once per engine, then assemble ONE report at `runs_dir/run_id` (BE-0076).

    The cross-browser fan-out: a loop over `engines`, each a full pass. `run_pass(engine, run_dir)`
    runs the selected scenarios for one engine against its own pool, writing that engine's evidence
    under `run_dir` (the caller hands it `runs_dir/run_id/<engine>`, prefixing the existing `NN-slug`
    layout so two engines never collide); its results are tagged with `engine` here. The passes'
    tagged results are concatenated into one flat list and written as a single manifest / JUnit /
    report — the manifest's `matrix` block aggregates the per-engine verdicts, and `ok` is
    all-must-pass across every engine x scenario (pure aggregation, no LLM).

    Returns:
        The concatenated per-engine results and the path to the written `manifest.json`.
    """
    run_dir = runs_dir / run_id
    # Owner-only up front (BE-0131): each engine pass writes under run_dir/<engine>, so a 0700 top
    # dir keeps every engine's evidence non-world-readable without per-subdir chmod.
    make_run_dir(run_dir)
    results: list[RunResult] = []
    for engine in engines:
        passed = run_pass(engine, run_dir / engine)
        for r in passed:
            r.engine = engine  # tag each verdict with its rendering engine for the matrix
            _reroot_evidence(r, engine)  # its evidence lives under <engine>/ in the one report
        results.extend(passed)
    manifest = _assemble_report(
        scenarios,
        results,
        run_dir,
        run_id,
        description=description,
        source_name=source_name,
        secret_values=secret_values,
        config_source=config_source,
        exec_provenance=exec_provenance,
    )
    return results, manifest


def _reroot_evidence(r: RunResult, engine: str) -> None:
    """Prefix a matrix result's run-dir-relative evidence paths with `<engine>/` (BE-0076).

    Each engine pass writes its evidence under `run_dir/<engine>/<sid>/`, but the artifact and
    visual-image paths are recorded relative to that pass's own `run_dir` (`<sid>/…`). The matrix
    assembles ONE report at the top `run_dir`, so every such path is re-rooted under the engine
    subtree here — otherwise the report's video / network / log / diff links resolve to the wrong
    directory. A no-op for paths already absent (None).
    """

    def artifact(a: Artifact) -> Artifact:
        return replace(a, name=f"{engine}/{a.name}")

    def visual(v: VisualEvidence | None) -> VisualEvidence | None:
        if v is None:
            return None
        return replace(
            v,
            actual=f"{engine}/{v.actual}",
            baseline=f"{engine}/{v.baseline}" if v.baseline else v.baseline,
            diff=f"{engine}/{v.diff}" if v.diff else v.diff,
        )

    def assertion(a: AssertionResult) -> AssertionResult:
        return replace(a, visual=visual(a.visual))

    r.artifacts = [artifact(a) for a in r.artifacts]
    r.expect_results = [assertion(a) for a in r.expect_results]
    for step in r.steps:
        step.artifacts = [artifact(a) for a in step.artifacts]
        step.assertion_results = [assertion(a) for a in step.assertion_results]


def _assemble_report(
    scenarios: list[Scenario],
    results: list[RunResult],
    run_dir: Path,
    run_id: str,
    *,
    source_name: str | None = None,
    description: str | None = None,
    secret_values: list[str] | None = None,
    config_source: dict[str, str] | None = None,
    exec_provenance: dict[str, str | None] | None = None,
) -> Path:
    """Write the run's report artifacts under `run_dir` from its (possibly engine-tagged) results.

    The shared report-writing tail of `run_and_report` and `run_matrix_and_report`: the executed
    `scenario.yaml`, the provenance stamps, and `manifest.json` / `junit.xml` / `report.html`,
    then the final secret-value scrub.
    """
    # Snapshot for evidence with literal `totp.secret` seeds masked (BE-0152) — a `${secrets.*}`
    # reference is kept and its resolved value is scrubbed by the secret-value pass below.
    snapshot = [redact_totp_secrets(s) for s in scenarios]
    # The merged Result tab renders each scenario as a structured view (definitions) with a toggle
    # to the raw YAML (sources). The same helper feeds the offline re-render, so the two match.
    definitions, sources = scenario_render_inputs(snapshot)
    make_run_dir(run_dir)  # owner-only; idempotent if run_and_report already created it (BE-0131)
    # Keep the executed scenario alongside its results (re-runnable / reviewable).
    scenario_yaml = dump_scenario_file(snapshot, description)
    scenario_path = run_dir / "scenario.yaml"
    scenario_path.write_text(scenario_yaml, encoding="utf-8")
    # The scenario copy can hold masked-but-sensitive text — owner-only, umask-independent (BE-0131).
    restrict_file(scenario_path)
    # Stamp the run's identity (scenario fingerprint + tool/git version) so accumulated runs can be
    # grouped to tell true flakiness from an edited scenario (BE-0049); pure metadata, never a verdict.
    provenance = run_provenance(
        scenario_yaml, git_revision=git_revision(), config_source=config_source
    )
    # Record what the upload-execution policy did with this run's launchServer command (BE-0090) —
    # denied / reused / sandboxed, and (when sandboxed) the image — so "what did this run execute,
    # and what was suppressed?" stays answerable. None for an ungoverned (local/Git) run.
    if exec_provenance is not None:
        provenance["uploadExec"] = exec_provenance
    manifest = write_report(
        run_dir,
        run_id,
        results,
        definitions,
        sources,
        source_name=source_name,
        description=description,
        provenance=provenance,
    )
    # Final safety net: scrub any literal secret value that reached a run-level artifact
    # (e.g. an assertion's expected/actual text in the manifest / HTML). The scenario
    # definitions already hold tokens, not values, so this only catches result text.
    _scrub_secret_values(run_dir, secret_values)
    return manifest


def _scrub_secret_values(run_dir: Path, secret_values: list[str] | None) -> None:
    if not secret_values:
        return
    scrub = Redactor(None, values=secret_values)
    for name in ("manifest.json", "junit.xml", "ctrf.json", "report.html", "scenario.yaml"):
        path = run_dir / name
        if path.exists():
            path.write_text(scrub.redact_text(path.read_text(encoding="utf-8")), encoding="utf-8")
