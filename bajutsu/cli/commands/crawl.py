"""`bajutsu crawl` — explore the app breadth-first and build a screen map (BE-0038).

The deterministic (`--guide off`) slice of BE-0038: no AI, no judgment. It drives the same
`launch_driver` / actuator path as `run` and `record`, hands the live driver to the crawl
engine ([`crawl.py`](../../crawl.py)), and streams the growing screen map to
`runs/<id>/screenmap.json` so the web UI can render it live. Discovery only — never a gate.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import typer

from bajutsu import crawl as crawl_engine
from bajutsu import env as _env
from bajutsu.backends import select_actuator
from bajutsu.cli._shared import DEFAULT_CONFIG, _backends, _load_effective
from bajutsu.drivers import base
from bajutsu.runner import _await_ready, launch_driver
from bajutsu.scenario import Preconditions


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
    erase: bool = typer.Option(
        True, "--erase/--no-erase", help="erase the device before launching (app must be installed)"
    ),
    out: str = typer.Option(
        "", "--out", help="run dir for the screen map (default: runs/<timestamp>)"
    ),
    config: str = typer.Option(DEFAULT_CONFIG),
) -> None:
    """Explore the app breadth-first over the deterministic crawl engine — no AI — writing a
    screen map (`screenmap.json`) of the reachable screens and the transitions between them.
    This is a discovery tool, never a pass/fail gate."""
    eff = _load_effective(config, app_name)
    try:
        actuator = select_actuator(_backends(backend, eff.backend))
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None

    out_dir = Path(out) if out else Path("runs") / datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Per-screen screenshots land here as `<fingerprint>.png`; the web UI shows each as a node
    # thumbnail (it builds the URL from the run id + fingerprint, so the map needs no extra field).
    screens_dir = out_dir / "screens"
    screens_dir.mkdir(exist_ok=True)
    screenmap_path = out_dir / "screenmap.json"
    _write_screenmap(
        screenmap_path, crawl_engine.ScreenMap()
    )  # an empty map the UI can poll at once
    typer.echo(f"crawl → {screenmap_path}")  # tells the web UI where the map lands

    udid = _env.resolve_udid(udid)

    # Narrate the otherwise-silent device work (reinstall + boot + launch) on stderr, like record.
    def say(msg: str) -> None:
        typer.echo(msg, err=True)

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
            f"crashes={len(screen_map.crashes)}"
        )

    def on_node(node: crawl_engine.Node) -> None:
        # Best-effort: a screenshot hiccup shouldn't abort the crawl (the node still maps fine).
        try:
            driver.screenshot(str(screens_dir / f"{node.fingerprint}.png"))
        except (OSError, subprocess.CalledProcessError) as exc:
            say(f"⚠️  screenshot failed for {node.fingerprint[:7]}: {exc}")

    say("✅ app is up — crawling…")
    try:
        screen_map = crawl_engine.crawl(
            driver,
            reset,
            max_screens=max_screens,
            max_steps=max_steps,
            on_event=on_event,
            on_node=on_node,
        )
    except _env.DeviceError as e:
        typer.echo(str(e))
        raise typer.Exit(2) from None
    _write_screenmap(screenmap_path, screen_map)
    typer.echo(
        f"crawled {len(screen_map.nodes)} screens, {len(screen_map.edges)} transitions, "
        f"{len(screen_map.crashes)} crashes -> {screenmap_path}"
    )


def register(app: typer.Typer) -> None:
    app.command()(crawl)
