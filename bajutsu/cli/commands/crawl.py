"""`bajutsu crawl` — explore the app breadth-first and build a screen map (BE-0038).

Drives the same `launch_driver` / actuator path as `run` and `record`, hands the live driver to
the crawl engine ([`crawl/core.py`](../../crawl/core.py)), and streams the growing screen map to
`runs/<id>/screenmap.json` so the web UI can render it live. On completion it also writes a
self-contained `runs/<id>/screenmap.html` (`crawl/report.py`) — the offline counterpart to the
live graph, openable straight from the run dir — plus one `runs/<id>/crashes/crash-NNN.yaml` repro
scenario per faithfully replayable crash (`crawl/repro.py`) and one `runs/<id>/flows/flow-NNN.yaml`
candidate scenario per faithfully reachable screen (`crawl/flows.py`), both directly runnable by
`run`. The engine is deterministic (screen identity, transitions, crashes); the AI guide only
proposes *what to try*, and the alert guard dismisses unexpected OS prompts. Discovery only — never a
pass/fail gate.

Like `run` after BE-0143, the body is a plan record plus small `_resolve_*`/`_wire_*` helpers
(BE-0205): each self-contained phase — warm-start resolution, lane planning, callback and health
wiring — is a named unit taking plain data, leaving `crawl` a thin readable sequence. The ~80-line
typer option signature stays inline (serve introspects its exact metadata via the BE-0134 flag
mirror).
"""

from __future__ import annotations

import atexit
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer

from bajutsu import crawl as crawl_engine
from bajutsu import device_errors
from bajutsu.ai import announce_ai
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _ai_redactor,
    _build_alert_guard,
    _install_usage_ledger,
    _load_effective_with_source,
    _refuse_out_in_checkout,
    _require_ai_credential,
    _resolve_language,
    _select_actuator_or_exit,
    _start_launch_server_or_exit,
    _with_headed,
    resolve_alert_handling_flag,
)
from bajutsu.config import Effective, web_base_url
from bajutsu.crawl import flows as crawl_flows
from bajutsu.crawl import report as crawl_report
from bajutsu.crawl import repro as crawl_repro
from bajutsu.crawl.guide import MODEL as _CRAWL_GUIDE_MODEL
from bajutsu.crawl.guide import Report, make_guide
from bajutsu.drivers import base
from bajutsu.evidence.redaction import Redactor
from bajutsu.platform_lifecycle import CrawlEnvironment, environment_for
from bajutsu.record import clear_blocking as clear_blocking_overlay
from bajutsu.run_id import new_run_id
from bajutsu.runner import launch_driver
from bajutsu.scenario import Preconditions


def _write_screenmap(path: Path, screen_map: crawl_engine.ScreenMap) -> None:
    """Atomically (re)write the screen map JSON.

    Write a sibling temp file then rename, so a concurrent reader (the web UI polling it) never sees
    a half-written file.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(crawl_engine.screenmap_dict(screen_map), indent=2), encoding="utf-8")
    tmp.replace(path)


def _resolve_warm_start(
    screenmap_path: Path,
    *,
    resume_src: str,
    resume_key: str,
    continue_crawl: bool,
    report: Report,
) -> tuple[
    crawl_engine.ScreenMap | None,
    list[crawl_engine.Action] | None,
    list[crawl_engine.Action] | None,
]:
    """Resolve how the crawl warm-starts, or return `(None, None, None)` for a fresh crawl.

    Two mutually-exclusive warm-start modes read the existing map from *screenmap_path* (`--out`
    points at the prior run); the caller has already rejected naming both (BE-0181):

    - `--resume-src`/`--resume-key` → single-branch resume of one pruned branch, returning its
      `(base_map, seed_path, seed_ops)` — replay to the branch's screen, then explore the pruned op.
    - `--continue` → full-frontier continuation, returning `(base_map, None, None)`; every screen the
      prior run left with untried operations is the frontier.

    A fresh crawl returns `(None, None, None)`; the caller writes the empty starter map.

    Raises:
        typer.Exit: the map can't be read, the named pruned branch is absent, or a continuation has
            no remaining frontier (exit code 2).
    """

    def _load_base_map(mode: str) -> crawl_engine.ScreenMap:
        # Both warm-start modes read the existing run's map from --out; only the failure prefix differs.
        try:
            data = json.loads(screenmap_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            typer.echo(f"{mode}: cannot read {screenmap_path}: {e}")
            raise typer.Exit(2) from None
        return crawl_engine.screenmap_from_dict(data)

    if resume_src and resume_key:
        base_map = _load_base_map("resume")
        match = next(
            (p for p in base_map.pruned if p.src == resume_src and p.key == resume_key), None
        )
        if match is None or not match.path:
            typer.echo(f"resume: no pruned branch {resume_key!r} on {resume_src[:7]}")
            raise typer.Exit(2)
        seed_path = list(match.path[:-1])  # replay to the screen
        seed_ops = [match.path[-1]]  # then the pruned op
        base_map.pruned = [p for p in base_map.pruned if p is not match]  # it's being explored now
        report(f"↩  resuming pruned branch: {match.action} on {resume_src[:7]}")
        return base_map, seed_path, seed_ops
    if continue_crawl:
        base_map = _load_base_map("continue")
        # The frontier is every screen the prior run left with untried operations; nothing to do if
        # it explored everything (stop_reason "completed"). Reject up front rather than launching a
        # device only to find no work.
        frontier = sum(1 for ops in base_map.plan.values() if ops)
        if not frontier:
            typer.echo(
                "continue: no remaining frontier in the screen map (nothing left to explore)"
            )
            raise typer.Exit(2)
        report(f"↪  continuing crawl: {frontier} screens with untried operations")
        return base_map, None, None
    return None, None, None


def _plan_lanes(
    environment: CrawlEnvironment,
    udid: str,
    workers: int,
    seed_path: list[crawl_engine.Action] | None,
) -> list[str]:
    """Resolve the crawl's worker-lane udids (one lane per udid, so `len` is the worker count).

    The platform's Environment sizes the lane set (BE-0009): iOS resolves the `--udid` pool and caps
    `--workers` to it (BE-0064); web has no device, so `--workers` alone sizes N browser lanes
    (BE-0077). Either way the returned pool is already capped to the worker count, so downstream reads
    its length rather than carrying a separate count. A single-branch resume (`seed_path` set) is one
    walk, so it collapses to a single lane (BE-0181).

    Raises:
        typer.Exit: `--udid` resolved to an empty pool (e.g. `--udid ""` / `--udid ,`) (exit code 2).
    """
    udids = environment.plan_lanes(udid, workers)
    if not udids:
        # An empty `--udid` resolves to no device — fail loudly with a fixable message rather than
        # crashing later on the first lane.
        typer.echo("no devices to crawl: --udid resolved to an empty pool")
        raise typer.Exit(2)
    if seed_path is not None:
        udids = udids[:1]
    return udids


def _make_callbacks(
    screenmap_path: Path, screens_dir: Path, report: Report
) -> tuple[crawl_engine.OnEvent, crawl_engine.OnNode]:
    """Build the crawl engine's `(on_event, on_node)` callbacks.

    `on_event` persists the growing map (so the web UI's poll sees each update) and streams a one-line
    progress summary. `on_node` screenshots each newly discovered screen on the discovering worker's
    own driver (a parallel crawl maps each screen on whichever device found it), best-effort: a
    screenshot hiccup — including a wedged web browser surfacing as a DeviceError — must not abort the
    crawl (the wedge resurfaces on the worker's next operation, where the engine relaunches the lane,
    BE-0077).
    """

    def on_event(screen_map: crawl_engine.ScreenMap) -> None:
        _write_screenmap(screenmap_path, screen_map)
        report(
            f"🔭 screens={len(screen_map.nodes)} transitions={len(screen_map.edges)} "
            f"crashes={len(screen_map.crashes)} alerts={len(screen_map.alerts)}"
        )

    def on_node(d: base.Driver, node: crawl_engine.Node) -> None:
        try:
            d.screenshot(str(screens_dir / f"{node.fingerprint}.png"))
        except (OSError, subprocess.CalledProcessError, device_errors.DeviceError) as exc:
            report(f"⚠️  screenshot failed for {node.fingerprint[:7]}: {exc}")

    return on_event, on_node


def _wire_health(
    environment: CrawlEnvironment,
    eff: Effective,
    redactor: Redactor,
    *,
    alert_handling: bool,
    alert_instruction: str,
    report: Report,
) -> tuple[
    crawl_engine.AliveCheck | None, crawl_engine.ClearBlocking | None, crawl_engine.Recover | None
]:
    """Wire the crawl's crash-detection, blocking-overlay clearing, and lane recovery seams.

    All three are platform-specific, behind the Environment seam (BE-0009): web reads deterministic
    signals (pageerror / HTTP status / blank DOM), auto-handles JS dialogs with no model, and
    relaunches a wedged browser (BE-0066/BE-0077); iOS reads the accessibility tree (engine default)
    and, when `--alert-handling` is on, clears OS prompts with the alert guard. The platform recovery
    is silent, so it's wrapped to report the wedge before healing the lane. On iOS the alert guard
    (Claude vision) supplies `clear_blocking`; the guide and the guard share one provider, and
    `_require_ai_credential` has already failed closed, so the guard's credential is known-present.
    """
    is_alive = environment.crawl_aliveness()
    clear_blocking = environment.crawl_dialog_clearer()
    recover = environment.crawl_recover()
    if recover is not None:
        heal = recover

        def recover(d: base.Driver) -> None:
            report("⚠️  a worker's browser wedged — relaunching it")
            heal(d)

    if clear_blocking is None and alert_handling:
        # The alert guard dismisses unexpected OS prompts the crawl would otherwise read as a crash.
        # `_require_ai_credential` has already failed closed, so the guard's credential is
        # known-present and the shared helper returns a real guard (never the no-op None branch).
        guard = _build_alert_guard(eff, redactor, alert_instruction)
        if guard is not None:
            from bajutsu.orchestrator import RealClock

            clock = RealClock()

            def clear_blocking(d: base.Driver) -> list[str]:
                return clear_blocking_overlay(d, guard, clock, report=report)

    return is_alive, clear_blocking, recover


@dataclass(frozen=True)
class _CrawlPlan:
    """Everything a resolved `crawl` needs to bring up its lanes and drive the engine — plain data.

    `crawl` fills this from the option flags via the `_resolve_*`/`_plan_lanes`/`_wire_health`
    helpers, then hands it to `_execute`. It carries resolved inputs only (config, actuator, the
    resolved paths, the warm-start result, and the lane pool), so each helper stays unit-testable
    without a Simulator.
    """

    eff: Effective
    actuator: str
    redactor: Redactor
    target_name: str
    out_dir: Path
    screens_dir: Path
    screenmap_path: Path
    environment: CrawlEnvironment
    udids: list[str]
    base_map: crawl_engine.ScreenMap | None
    seed_path: list[crawl_engine.Action] | None
    seed_ops: list[crawl_engine.Action] | None
    max_screens: int
    max_steps: int
    prune_global: bool
    erase: bool
    alert_handling: bool
    alert_instruction: str
    upload_exec: str


def _execute(plan: _CrawlPlan, guide: crawl_engine.Guide, report: Report) -> crawl_engine.ScreenMap:
    """Bring up the lanes and drive the crawl engine, returning the final screen map.

    Starts the app's target server (if the target declares one), builds the primary lane on this
    thread (its driver drives bootstrap and the in-place walk) plus lazy per-thread factories for the
    extra lanes (BE-0064 sims / BE-0077 browsers), wires the callbacks and health seams, then runs the
    engine and writes the final map.

    Raises:
        typer.Exit: the launch server or a device lane fails to come up (exit code 2).
    """
    # Bring up the app's target server (the web baseUrl host) if it declares launchServer — reused
    # if already serving, started otherwise (waiting on its readiness probe). Stopped when this
    # command exits (atexit), since the crawl is a single linear flow with no run-style teardown.
    stop_server, _exec_decision = _start_launch_server_or_exit(
        plan.eff, upload_exec=plan.upload_exec or None
    )
    atexit.register(stop_server)

    workers = len(plan.udids)  # one lane per resolved udid (the pool is already capped)
    if not plan.environment.has_devices():
        browsers = f"{workers} browsers" if workers > 1 else "the browser"
        report(f"⚙️  preparing {browsers} — navigating to {web_base_url(plan.eff)} …")
    elif workers > 1:
        report(
            f"⚙️  preparing {workers} simulators — installing and launching {plan.target_name} "
            "on each (this can take a moment) …"
        )
    else:
        report(
            f"⚙️  preparing the simulator — installing and launching {plan.target_name} "
            "(this can take a moment) …"
        )

    def build_lane(u: str) -> tuple[base.Driver, crawl_engine.Reset]:
        # The crawl `reset` (revisit a known screen from a clean start) is the platform's, behind the
        # Environment seam (BE-0009): web opens a fresh context, iOS relaunches the app.
        driver, _readiness = launch_driver(
            u, plan.eff, plan.actuator, Preconditions(erase=plan.erase)
        )
        return driver, environment_for(plan.actuator, u).crawl_reset(plan.eff)

    try:
        # The primary lane is built here (on the main thread): it drives bootstrap and the in-place
        # walk, so its driver lives on this thread. A launch failure surfaces cleanly as exit 2.
        primary = build_lane(plan.udids[0])
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    # Extra lanes are built lazily *inside their own worker thread* (BE-0077): a Playwright browser
    # must be created on the thread that drives it, and the iOS backend is thread-agnostic so this is harmless
    # for iOS. A factory's own launch failure is surfaced by the engine after join.
    def lane_factory(u: str) -> crawl_engine.WorkerFactory:
        return lambda: build_lane(u)

    extra_factories = [lane_factory(u) for u in plan.udids[1:]]

    on_event, on_node = _make_callbacks(plan.screenmap_path, plan.screens_dir, report)
    is_alive, clear_blocking, recover = _wire_health(
        plan.environment,
        plan.eff,
        plan.redactor,
        alert_handling=plan.alert_handling,
        alert_instruction=plan.alert_instruction,
        report=report,
    )

    report("✅ app is up — crawling…")
    try:
        screen_map = crawl_engine.crawl(
            primary[0],
            primary[1],
            max_screens=plan.max_screens,
            max_steps=plan.max_steps,
            clear_blocking=clear_blocking,
            is_alive=is_alive,
            guide=guide,
            # A single-branch resume explores that one branch fully (no pruning); a fresh crawl and a
            # full-frontier continuation both prune duplicate global controls per the flag (BE-0181).
            prune_global=plan.prune_global and plan.seed_path is None,
            base_map=plan.base_map,
            seed_path=plan.seed_path,
            seed_ops=plan.seed_ops,
            on_event=on_event,
            on_node=on_node,
            recover=recover,  # web: relaunch a wedged browser so its lane keeps crawling (BE-0077)
            extra_workers=extra_factories,  # built on their own threads (BE-0064 sims / BE-0077 browsers)
        )
    except device_errors.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    _write_screenmap(plan.screenmap_path, screen_map)
    return screen_map


def _finish(plan: _CrawlPlan, screen_map: crawl_engine.ScreenMap) -> None:
    """Write the offline artifacts (report, crash repros, candidate flows) and echo the summary.

    The screen map is discovery output, never a pass/fail verdict, so this runs unconditionally after
    the crawl: the self-contained HTML report, one repro scenario per faithfully replayable crash, and
    one candidate flow per reachable screen (all directly runnable by `run`), then a one-line summary
    naming why the crawl stopped.
    """
    report_path = crawl_report.write_html(plan.out_dir, screen_map, plan.out_dir.name)
    repros = crawl_repro.write_repros(plan.out_dir, screen_map)
    flows = crawl_flows.write_flows(plan.out_dir, screen_map)
    why = {
        "completed": "explored everything reachable",
        "max_screens": f"reached the --max-screens limit ({plan.max_screens})",
        "max_steps": f"reached the --max-steps limit ({plan.max_steps})",
    }.get(screen_map.stop_reason, screen_map.stop_reason or "stopped")
    typer.echo(
        f"crawled {len(screen_map.nodes)} screens, {len(screen_map.edges)} transitions, "
        f"{len(screen_map.crashes)} crashes, {len(screen_map.alerts)} alerts dismissed "
        f"({why}) -> {plan.screenmap_path}"
    )
    typer.echo(f"screen map report -> {report_path}")
    if repros:
        typer.echo(f"crash repro scenarios -> {len(repros)} under {plan.out_dir / 'crashes'}")
    if flows:
        typer.echo(f"candidate flow scenarios -> {len(flows)} under {plan.out_dir / 'flows'}")


def crawl(
    target_name: str = typer.Option(..., "--target"),
    udid: str = typer.Option(
        "booted",
        help="simulator(s) to crawl on: a single udid, or a comma list for a parallel pool "
        "(see --workers); mirrors `run`'s --udid",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        help="crawl with this many workers at once, sharing one screen map: across this many "
        "simulators on iOS (BE-0064, capped to the --udid devices), or this many browser processes "
        "on web (BE-0077). Default 1 = single-worker crawl.",
    ),
    backend: str = typer.Option(""),
    max_screens: int = typer.Option(
        50, "--max-screens", help="stop after discovering this many distinct screens"
    ),
    max_steps: int = typer.Option(200, "--max-steps", help="stop after taking this many actions"),
    prune_global: bool = typer.Option(
        True,
        "--prune-global/--no-prune-global",
        help="explore a global control (a tab/nav reused across screens) once instead of from every "
        "screen that shows it (on by default; the WebUI lets you resume a pruned branch on demand)",
    ),
    erase: bool = typer.Option(
        True, "--erase/--no-erase", help="erase the device before launching (app must be installed)"
    ),
    alert_handling: bool | None = typer.Option(
        None,
        "--alert-handling/--no-alert-handling",
        help="handle unexpected OS prompts while crawling (on by default; uses the same API key)",
    ),
    dismiss_alerts: bool | None = typer.Option(
        None,
        "--dismiss-alerts/--no-dismiss-alerts",
        hidden=True,
        help="deprecated alias for --alert-handling (BE-0317)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    out: str = typer.Option(
        "", "--out", help="run dir for the screen map (default: runs/<timestamp>)"
    ),
    resume_src: str = typer.Option(
        "",
        "--resume-src",
        help="resume exploring a pruned branch: the screen fingerprint it was "
        "pruned on (with --resume-key and --out pointing at the existing run)",
    ),
    resume_key: str = typer.Option(
        "", "--resume-key", help="resume: the pruned operation's replay key (see --resume-src)"
    ),
    continue_crawl: bool = typer.Option(
        False,
        "--continue",
        help="continue an existing run's whole remaining frontier — every screen with untried "
        "operations, not one pruned branch — with --out pointing at that run; raise "
        "--max-screens/--max-steps to go deeper, and --workers/--udid runs the continuation in "
        "parallel. Mutually exclusive with --resume-src/--resume-key.",
    ),
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: crawl a visible (headed, slow-motion) browser instead of headless; "
        "default leaves the target's `headless` config",
    ),
    language: str = typer.Option(
        "",
        "--language",
        help="AI output language for the guide's streamed reasoning — ja / en / auto; overrides "
        "`ai.language`, default leaves the config (auto stays English for crawl)",
    ),
    upload_exec: str = typer.Option(
        "",
        "--upload-exec",
        hidden=True,
        help="internal: serve sets this for an uploaded bundle to govern its launchServer command "
        "(deny | reuse | sandbox); empty = ungoverned local/Git run (BE-0090)",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app breadth-first and write a screen map (`screenmap.json`).

    Maps the reachable screens and the transitions between them. The engine is deterministic (screen
    identity, transitions, crashes); the AI guide only proposes *what to try*. A discovery tool,
    never a pass/fail gate.
    """
    # Argument validation first, before any config/backend/credential/device setup: --continue takes
    # an existing run's whole remaining frontier, --resume-src/--resume-key one pruned branch — naming
    # both is contradictory, so reject it up front (BE-0181).
    if continue_crawl and (resume_src or resume_key):
        typer.echo("crawl: --continue and --resume-src/--resume-key are mutually exclusive")
        raise typer.Exit(2)
    eff, _source, checkout_root = _load_effective_with_source(config, target_name)
    # --headed/--no-headed overrides the target's `headless` config (web backend only; iOS ignores it).
    eff = _with_headed(eff, headed)
    # --language overrides the target's `ai.language` (flag > config > auto), BE-0188.
    eff = _resolve_language(eff, language)

    # Progress (device work + the AI guide's reasoning) goes to stderr, like record's stream; the
    # web UI merges it into the crawl log so a watcher sees what the AI is thinking, turn by turn.
    def say(msg: str) -> None:
        typer.echo(msg, err=True)

    actuator, _ = _select_actuator_or_exit(backend, eff, [])
    # Fail closed (BE-0097 / BE-0047): the guide and the alert guard both reach the model via the
    # resolved SDK provider (Anthropic / Bedrock / ant), so a missing credential is an actionable
    # error, not a quiet fallback. Placed after backend selection so a config/backend error surfaces
    # first.
    _require_ai_credential(eff)
    redactor = _ai_redactor(eff)
    # Attribute the crawl guide's (and alert guard's) AI tokens/cost to the `crawl` command (BE-0196).
    _install_usage_ledger(eff, "crawl", scenario=target_name)
    announce_ai(say, default_model=_CRAWL_GUIDE_MODEL, ai=eff.ai)
    crawl_guide = make_guide(report=say, ai=eff.ai, redactor=redactor)

    out_dir = Path(out) if out else Path("runs") / new_run_id()
    # A Git source is read-only input: the screen map / screenshots go to a local run dir, never into
    # the SHA-keyed checkout cache (BE-0063). The default `runs/` is already local.
    _refuse_out_in_checkout(out_dir, checkout_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Per-screen screenshots land here as `<fingerprint>.png`; the web UI shows each as a node
    # thumbnail (it builds the URL from the run id + fingerprint, so the map needs no extra field).
    screens_dir = out_dir / "screens"
    screens_dir.mkdir(exist_ok=True)
    screenmap_path = out_dir / "screenmap.json"

    # Warm-start from an existing run's map (--out points at it), or a fresh crawl otherwise. A fresh
    # crawl writes the empty starter map the UI can poll now.
    base_map, seed_path, seed_ops = _resolve_warm_start(
        screenmap_path,
        resume_src=resume_src,
        resume_key=resume_key,
        continue_crawl=continue_crawl,
        report=say,
    )
    if base_map is None:
        _write_screenmap(screenmap_path, crawl_engine.ScreenMap())
    typer.echo(f"crawl → {screenmap_path}")  # tells the web UI where the map lands

    # The worker pool, all sharing one screen map. The pool-level Environment (no specific udid) sizes
    # the lane set and supplies the health seams; per-lane environments build each lane's reset.
    environment = environment_for(actuator, "")
    udids = _plan_lanes(environment, udid, workers, seed_path)

    # On by default while crawling; the shared resolver folds in the deprecated --dismiss-alerts.
    alert_handling_enabled = resolve_alert_handling_flag(
        alert_handling, dismiss_alerts, default=True
    )

    plan = _CrawlPlan(
        eff=eff,
        actuator=actuator,
        redactor=redactor,
        target_name=target_name,
        out_dir=out_dir,
        screens_dir=screens_dir,
        screenmap_path=screenmap_path,
        environment=environment,
        udids=udids,
        base_map=base_map,
        seed_path=seed_path,
        seed_ops=seed_ops,
        max_screens=max_screens,
        max_steps=max_steps,
        prune_global=prune_global,
        erase=erase,
        alert_handling=alert_handling_enabled,
        alert_instruction=alert_instruction,
        upload_exec=upload_exec,
    )
    screen_map = _execute(plan, crawl_guide, say)
    _finish(plan, screen_map)


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(crawl)
