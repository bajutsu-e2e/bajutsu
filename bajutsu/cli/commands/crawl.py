"""`bajutsu crawl` — explore the app breadth-first and build a screen map (BE-0038).

Drives the same `launch_driver` / actuator path as `run` and `record`, hands the live driver to
the crawl engine ([`crawl.py`](../../crawl.py)), and streams the growing screen map to
`runs/<id>/screenmap.json` so the web UI can render it live. The engine is deterministic (screen
identity, transitions, crashes); the AI guide only proposes *what to try*, and the alert guard
dismisses unexpected OS prompts. Discovery only — never a pass/fail gate.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import crawl as crawl_engine
from bajutsu import env as _env
from bajutsu.agents import AGENT_KINDS, resolve_kind
from bajutsu.anthropic_client import credential_gap
from bajutsu.backends import select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.crawl_guide import make_guide
from bajutsu.drivers import base
from bajutsu.record import _clear_blocking
from bajutsu.runner import _await_ready, launch_driver
from bajutsu.scenario import Preconditions


def _ai_credential_gap(agent: str) -> str | None:
    """The crawl guide's credential gap for `--agent` (BE-0053: crawl is a Tier-1 Bedrock path):
    `claude-code` brings its own auth (always None), while `api` uses the Anthropic SDK and so needs
    the configured provider's credentials — see `credential_gap`."""
    return None if agent != "api" else credential_gap()


def _write_screenmap(path: Path, screen_map: crawl_engine.ScreenMap) -> None:
    """Atomically (re)write the screen map JSON: write a sibling temp file then rename, so a
    concurrent reader (the web UI polling it) never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(crawl_engine.screenmap_dict(screen_map), indent=2), encoding="utf-8")
    tmp.replace(path)


def crawl(
    app_name: str = typer.Option(..., "--app"),
    udid: str = typer.Option("booted"),
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
        "default leaves the app's `headless` config",
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app breadth-first and write a screen map (`screenmap.json`) of the reachable
    screens and the transitions between them. The engine is deterministic (screen identity,
    transitions, crashes); the AI guide only proposes *what to try*. A discovery tool, never a
    pass/fail gate."""
    eff = _load_effective(config, app_name)
    # --headed/--no-headed overrides the app's `headless` config (web backend only; iOS ignores it).
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
    # Crawl is AI-driven: the AI proposes what to try (the engine keeps identity/transitions/crashes
    # deterministic). The default backend needs an API key; --agent claude-code uses its own auth.
    crawl_guide = make_guide(report=say, agent=agent)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    # `--agent api` reaches Claude through the configured AI provider (BE-0053): ANTHROPIC_API_KEY
    # for Anthropic, or AWS credentials + a provider-prefixed BAJUTSU_BEDROCK_MODEL for Bedrock.
    # Fail fast with provider-specific guidance before any device work (claude-code brings its own).
    gap = _ai_credential_gap(agent)
    if gap == "anthropic-key":
        typer.echo("note: crawl --agent api needs ANTHROPIC_API_KEY for the Anthropic provider —")
        typer.echo("      set it, use --agent claude-code (the Claude Code CLI), or select Amazon")
        typer.echo("      Bedrock (BAJUTSU_AI_PROVIDER=bedrock, authenticated by AWS credentials)")
        raise typer.Exit(2)
    if gap == "bedrock-model":
        typer.echo("note: crawl --agent api on Bedrock (BAJUTSU_AI_PROVIDER=bedrock) needs")
        typer.echo("      BAJUTSU_BEDROCK_MODEL set to a provider-prefixed model id")
        typer.echo("      (e.g. global.anthropic.claude-opus-4-6-v1)")
        raise typer.Exit(2)

    out_dir = Path(out) if out else Path("runs") / datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
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

    udid = _env.resolve_udid(udid)

    say(
        f"⚙️  preparing the simulator — installing and launching {app_name} (this can take a moment) …"
    )
    try:
        driver = launch_driver(udid, eff, actuator, Preconditions(erase=erase))
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    def reset(d: base.Driver) -> None:
        # Revisit a known screen the way `run` reaches any state — return to a clean start and let
        # the engine replay the shortest path. A relaunch (not a full erase) keeps each frontier
        # visit fast; the app's own UI returns to its entry screen.
        e = _env.Env(udid)
        e.terminate(eff.bundle_id)
        e.launch(eff.bundle_id, [*eff.launch_args, *_env.locale_args(eff.locale)], eff.launch_env)
        _await_ready(d)

    def on_event(screen_map: crawl_engine.ScreenMap) -> None:
        _write_screenmap(screenmap_path, screen_map)
        say(
            f"🔭 screens={len(screen_map.nodes)} transitions={len(screen_map.edges)} "
            f"crashes={len(screen_map.crashes)} alerts={len(screen_map.alerts)}"
        )

    def on_node(node: crawl_engine.Node) -> None:
        # Best-effort: a screenshot hiccup shouldn't abort the crawl (the node still maps fine).
        try:
            driver.screenshot(str(screens_dir / f"{node.fingerprint}.png"))
        except (OSError, subprocess.CalledProcessError) as exc:
            say(f"⚠️  screenshot failed for {node.fingerprint[:7]}: {exc}")

    # The alert guard (Claude vision) dismisses unexpected OS prompts the crawl would otherwise
    # read as a crash. Best-effort: with no API key it no-ops, so the crawl still runs.
    clear_blocking = None
    if dismiss_alerts:
        from bajutsu.alerts import ClaudeAlertLocator, SystemAlertGuard
        from bajutsu.orchestrator import RealClock

        # The alert guard always uses the Anthropic SDK (vision), so it needs the configured
        # provider's credentials regardless of --agent. Warn (best-effort) if they're missing.
        guard_gap = credential_gap()
        if guard_gap == "anthropic-key":
            say(
                "note: dismiss-alerts is on but ANTHROPIC_API_KEY is unset — the alert guard no-ops"
            )
        elif guard_gap == "bedrock-model":
            say(
                "note: dismiss-alerts is on but BAJUTSU_BEDROCK_MODEL is unset — "
                "the alert guard no-ops"
            )
        guard = SystemAlertGuard(ClaudeAlertLocator(), alert_instruction or None).dismiss
        clock = RealClock()

        def clear_blocking(d: base.Driver) -> list[str]:
            return _clear_blocking(d, guard, clock, report=say)

    say("✅ app is up — crawling…")
    try:
        screen_map = crawl_engine.crawl(
            driver,
            reset,
            max_screens=max_screens,
            max_steps=max_steps,
            clear_blocking=clear_blocking,
            guide=crawl_guide,
            prune_global=prune_global and base_map is None,  # resume explores the branch fully
            base_map=base_map,
            seed_path=seed_path,
            seed_ops=seed_ops,
            on_event=on_event,
            on_node=on_node,
        )
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    _write_screenmap(screenmap_path, screen_map)
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


def register(app: typer.Typer) -> None:
    app.command()(crawl)
