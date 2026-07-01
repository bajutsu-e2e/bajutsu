"""`bajutsu crawl` — explore the app breadth-first and build a screen map (BE-0038).

Drives the same `launch_driver` / actuator path as `run` and `record`, hands the live driver to
the crawl engine ([`crawl.py`](../../crawl.py)), and streams the growing screen map to
`runs/<id>/screenmap.json` so the web UI can render it live. On completion it also writes a
self-contained `runs/<id>/screenmap.html` (`crawl_report.py`) — the offline counterpart to the
live graph, openable straight from the run dir — plus one `runs/<id>/crashes/crash-NNN.yaml` repro
scenario per faithfully replayable crash (`crawl_repro.py`), directly runnable by `run`. The engine
is deterministic (screen identity, transitions, crashes); the AI guide only proposes *what to try*,
and the alert guard dismisses unexpected OS prompts. Discovery only — never a pass/fail gate.
"""

from __future__ import annotations

import atexit
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import crawl as crawl_engine
from bajutsu import crawl_report, crawl_repro
from bajutsu import env as _env
from bajutsu.agents import AGENT_KINDS, resolve_kind
from bajutsu.anthropic_client import credential_gap, key_env
from bajutsu.backends import ensure_web_runtime, select_actuator
from bajutsu.cli._shared import (
    DEFAULT_CONFIG,
    _ai_redactor,
    _backends,
    _load_effective_with_source,
    _refuse_out_in_checkout,
    _require_ai_credential,
)
from bajutsu.crawl_guide import make_guide
from bajutsu.drivers import base
from bajutsu.environment import environment_for
from bajutsu.record import _clear_blocking
from bajutsu.runner import launch_driver
from bajutsu.runner.launch_server import start_launch_server
from bajutsu.scenario import Preconditions


def _write_screenmap(path: Path, screen_map: crawl_engine.ScreenMap) -> None:
    """Atomically (re)write the screen map JSON.

    Write a sibling temp file then rename, so a concurrent reader (the web UI polling it) never sees
    a half-written file.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(crawl_engine.screenmap_dict(screen_map), indent=2), encoding="utf-8")
    tmp.replace(path)


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
    dismiss_alerts: bool = typer.Option(
        True,
        "--dismiss-alerts/--no-dismiss-alerts",
        help="dismiss unexpected OS prompts while crawling (on by default; uses the same API key)",
    ),
    alert_instruction: str = typer.Option(
        "", "--alert-instruction", help="how to handle a prompt instead of dismissing it"
    ),
    agent: str = typer.Option(
        "",
        "--agent",
        help="AI backend for the crawl guide: 'api' (the Anthropic SDK, pay-per-token; uses the "
        "configured AI provider — ANTHROPIC_API_KEY for Anthropic, or AWS credentials + "
        "BAJUTSU_BEDROCK_MODEL when BAJUTSU_AI_PROVIDER=bedrock) or 'claude-code' (the Claude Code "
        "CLI, drawing on your subscription; text-only). Defaults to $BAJUTSU_AGENT or 'api'.",
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
    headed: bool | None = typer.Option(
        None,
        "--headed/--no-headed",
        help="web backend: crawl a visible (headed, slow-motion) browser instead of headless; "
        "default leaves the target's `headless` config",
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
    eff, _source, checkout_root = _load_effective_with_source(config, target_name)
    # --headed/--no-headed overrides the target's `headless` config (web backend only; iOS ignores it).
    if headed is not None:
        eff = replace(eff, headless=not headed)

    # Progress (device work + the AI guide's reasoning) goes to stderr, like record's stream; the
    # web UI merges it into the crawl log so a watcher sees what the AI is thinking, turn by turn.
    def say(msg: str) -> None:
        typer.echo(msg, err=True)

    # Explicit --agent wins, else $BAJUTSU_AGENT (set by serve's Settings selector), else api.
    agent = resolve_kind(agent)
    if agent not in AGENT_KINDS:
        typer.echo(f"unknown --agent {agent!r} (use {' or '.join(AGENT_KINDS)})")
        raise typer.Exit(2)
    backends = _backends(backend, eff.backend)
    try:
        # Auto-install Playwright (and the selected engine's browser) if a web crawl needs it.
        ensure_web_runtime(backends, eff.browser)
        actuator = select_actuator(backends)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # Fail closed (BE-0097 / BE-0047): the API guide and the alert guard both reach the model via
    # the SDK provider, so a missing credential is an actionable error, not a quiet fallback.
    # Placed after backend selection so a config/backend error surfaces first.
    if agent == "api":
        _require_ai_credential(eff)
    redactor = _ai_redactor(eff)
    crawl_guide = make_guide(report=say, agent=agent, ai=eff.ai, redactor=redactor)

    out_dir = Path(out) if out else Path("runs") / datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    # A Git source is read-only input: the screen map / screenshots go to a local run dir, never into
    # the SHA-keyed checkout cache (BE-0063). The default `runs/` is already local.
    _refuse_out_in_checkout(out_dir, checkout_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Per-screen screenshots land here as `<fingerprint>.png`; the web UI shows each as a node
    # thumbnail (it builds the URL from the run id + fingerprint, so the map needs no extra field).
    screens_dir = out_dir / "screens"
    screens_dir.mkdir(exist_ok=True)
    screenmap_path = out_dir / "screenmap.json"

    # Resume mode: continue from the existing map, exploring one pruned branch. Else start fresh.
    base_map = seed_path = seed_ops = None
    if resume_src and resume_key:
        try:
            data = json.loads(screenmap_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            typer.echo(f"resume: cannot read {screenmap_path}: {e}")
            raise typer.Exit(2) from None
        base_map = crawl_engine.screenmap_from_dict(data)
        match = next(
            (p for p in base_map.pruned if p.src == resume_src and p.key == resume_key), None
        )
        if match is None or not match.path:
            typer.echo(f"resume: no pruned branch {resume_key!r} on {resume_src[:7]}")
            raise typer.Exit(2)
        seed_path = list(match.path[:-1])  # replay to the screen
        seed_ops = [match.path[-1]]  # then the pruned op
        base_map.pruned = [p for p in base_map.pruned if p is not match]  # it's being explored now
        say(f"↩  resuming pruned branch: {match.action} on {resume_src[:7]}")
    else:
        _write_screenmap(screenmap_path, crawl_engine.ScreenMap())  # empty map the UI can poll now
    typer.echo(f"crawl → {screenmap_path}")  # tells the web UI where the map lands

    # The worker pool, all sharing one screen map. The platform's Environment sizes the lane set
    # (BE-0009): iOS resolves the `--udid` pool and caps `--workers` to it (BE-0064); web has no
    # device, so the worker count alone sizes N browser lanes (BE-0077). A resumed crawl is a
    # single-branch walk, so it runs on one lane.
    environment = environment_for(actuator, "")
    udids = environment.plan_lanes(udid, workers)
    if not udids:
        # An empty `--udid` (e.g. `--udid ""` / `--udid ,`) resolves to no device — fail loudly with
        # a fixable message rather than crashing later on the first lane.
        typer.echo("no devices to crawl: --udid resolved to an empty pool")
        raise typer.Exit(2)
    if base_map is not None:
        udids = udids[:1]
    workers = len(udids)

    # Bring up the app's target server (the web baseUrl host) if it declares launchServer — reused
    # if already serving, started otherwise (waiting on its readiness probe). Stopped when this
    # command exits (atexit), since the crawl is a single linear flow with no run-style teardown.
    try:
        stop_server, _exec_decision = start_launch_server(eff, upload_exec=upload_exec or None)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    atexit.register(stop_server)

    if not environment.has_devices():
        browsers = f"{workers} browsers" if workers > 1 else "the browser"
        say(f"⚙️  preparing {browsers} — navigating to {eff.base_url} …")
    elif workers > 1:
        say(
            f"⚙️  preparing {workers} simulators — installing and launching {target_name} on each "
            "(this can take a moment) …"
        )
    else:
        say(
            f"⚙️  preparing the simulator — installing and launching {target_name} "
            "(this can take a moment) …"
        )

    def build_lane(u: str) -> tuple[base.Driver, crawl_engine.Reset]:
        # The crawl `reset` (revisit a known screen from a clean start) is the platform's, behind the
        # Environment seam (BE-0009): web opens a fresh context, iOS relaunches the app.
        driver = launch_driver(u, eff, actuator, Preconditions(erase=erase))
        return driver, environment_for(actuator, u).crawl_reset(eff)

    try:
        # The primary lane is built here (on the main thread): it drives bootstrap and the in-place
        # walk, so its driver lives on this thread. A launch failure surfaces cleanly as exit 2.
        primary = build_lane(udids[0])
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    # Extra lanes are built lazily *inside their own worker thread* (BE-0077): a Playwright browser
    # must be created on the thread that drives it, and idb is thread-agnostic so this is harmless
    # for iOS. A factory's own launch failure is surfaced by the engine after join.
    def lane_factory(u: str) -> crawl_engine.WorkerFactory:
        return lambda: build_lane(u)

    extra_factories = [lane_factory(u) for u in udids[1:]]

    def on_event(screen_map: crawl_engine.ScreenMap) -> None:
        _write_screenmap(screenmap_path, screen_map)
        say(
            f"🔭 screens={len(screen_map.nodes)} transitions={len(screen_map.edges)} "
            f"crashes={len(screen_map.crashes)} alerts={len(screen_map.alerts)}"
        )

    def on_node(d: base.Driver, node: crawl_engine.Node) -> None:
        # Screenshot on the worker's own driver (a parallel crawl maps each screen on whichever
        # device found it). Best-effort: a screenshot hiccup shouldn't abort the crawl. A wedged web
        # browser surfaces here as a DeviceError too — skip the (non-essential) screenshot; the wedge
        # resurfaces on the worker's next operation, where the engine relaunches the lane (BE-0077).
        try:
            d.screenshot(str(screens_dir / f"{node.fingerprint}.png"))
        except (OSError, subprocess.CalledProcessError, _env.DeviceError) as exc:
            say(f"⚠️  screenshot failed for {node.fingerprint[:7]}: {exc}")

    # Crash detection and blocking-overlay clearing are platform-specific, behind the Environment
    # seam (BE-0009): web reads deterministic signals (pageerror / HTTP status / blank DOM), auto-
    # handles JS dialogs with no model, and relaunches a wedged browser (BE-0066/BE-0077); iOS reads
    # the accessibility tree (engine default) and, when asked, clears OS prompts with the alert guard.
    is_alive: crawl_engine.AliveCheck | None = environment.crawl_aliveness()
    clear_blocking: crawl_engine.ClearBlocking | None = environment.crawl_dialog_clearer()
    recover: crawl_engine.Recover | None = environment.crawl_recover()
    if recover is not None:
        # The platform recovery is silent; the crawl reports the wedge before healing the lane.
        heal = recover

        def recover(d: base.Driver) -> None:
            say("⚠️  a worker's browser wedged — relaunching it")
            heal(d)

    if clear_blocking is None and dismiss_alerts:
        # The alert guard (Claude vision) dismisses unexpected OS prompts the crawl would otherwise
        # read as a crash. Best-effort: with no API key it no-ops, so the crawl still runs.
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard
        from bajutsu.orchestrator import RealClock

        # Provider-aware credential check (BE-0097): the alert guard always uses the SDK, so it
        # needs the configured provider's credentials regardless of --agent.
        guard_gap = credential_gap(eff.ai)
        if guard_gap == "anthropic-key":
            say(
                f"note: dismiss-alerts is on but ${key_env(eff.ai)} is unset — "
                "the alert guard no-ops"
            )
        elif guard_gap == "bedrock-model":
            say(
                "note: dismiss-alerts is on but BAJUTSU_BEDROCK_MODEL is unset — "
                "the alert guard no-ops"
            )
        locator = ClaudeAlertLocator(ai=eff.ai, redactor=redactor)
        guard = SystemAlertGuard(locator, alert_instruction or None).dismiss
        clock = RealClock()

        def clear_blocking(d: base.Driver) -> list[str]:
            return _clear_blocking(d, guard, clock, report=say)

    say("✅ app is up — crawling…")
    try:
        screen_map = crawl_engine.crawl(
            primary[0],
            primary[1],
            max_screens=max_screens,
            max_steps=max_steps,
            clear_blocking=clear_blocking,
            is_alive=is_alive,
            guide=crawl_guide,
            prune_global=prune_global and base_map is None,  # resume explores the branch fully
            base_map=base_map,
            seed_path=seed_path,
            seed_ops=seed_ops,
            on_event=on_event,
            on_node=on_node,
            recover=recover,  # web: relaunch a wedged browser so its lane keeps crawling (BE-0077)
            extra_workers=extra_factories,  # built on their own threads (BE-0064 sims / BE-0077 browsers)
        )
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    _write_screenmap(screenmap_path, screen_map)
    report_path = crawl_report.write_html(out_dir, screen_map, out_dir.name)
    repros = crawl_repro.write_repros(out_dir, screen_map)
    why = {
        "completed": "explored everything reachable",
        "max_screens": f"reached the --max-screens limit ({max_screens})",
        "max_steps": f"reached the --max-steps limit ({max_steps})",
    }.get(screen_map.stop_reason, screen_map.stop_reason or "stopped")
    typer.echo(
        f"crawled {len(screen_map.nodes)} screens, {len(screen_map.edges)} transitions, "
        f"{len(screen_map.crashes)} crashes, {len(screen_map.alerts)} alerts dismissed "
        f"({why}) -> {screenmap_path}"
    )
    typer.echo(f"screen map report -> {report_path}")
    if repros:
        typer.echo(f"crash repro scenarios -> {len(repros)} under {out_dir / 'crashes'}")


def register(app: typer.Typer) -> None:
    """Register this command on the Typer app."""
    app.command()(crawl)
