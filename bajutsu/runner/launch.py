"""Bring a device up and launch the app: erase/boot/install/launch, then wait until ready."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from bajutsu import simctl
from bajutsu.config import Effective
from bajutsu.drivers import base

# Readiness polling lives with the platform lifecycle now (BE-0009 Phase 0); re-exported here so
# `from bajutsu.runner import _await_ready` and the crawl path keep their import unchanged.
from bajutsu.platform_lifecycle import RunEnvironment, _await_ready, environment_for
from bajutsu.scenario import Preconditions

__all__ = ["_await_ready", "launch_driver"]


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: simctl.RunFn = simctl._real_run,
    extra_env: Mapping[str, str] | None = None,
    record_video_dir: Path | None = None,
) -> base.Driver:
    """Bring a device up, launch the app under config + scenario env, and return a ready driver.

    The iOS backend runs the simctl lifecycle (erase → boot → install → launch). simctl `erase`
    needs a shut-down device, so an erase run shuts down first (shutdown → erase → boot); any simctl
    step that fails (e.g. the app isn't installed) is surfaced as a clean `simctl.DeviceError` so the
    CLI exits 2 instead of dumping a traceback. The web backend has no device to boot: a fresh
    browser context is the clean state and `navigate()` is the launch.

    Args:
        udid: The booted Simulator's udid; the web backend ignores it (one browser lane).
        eff: The resolved target config (bundle id / baseUrl, launch env/args, app path, locale).
        actuator: The selected actuator (`idb` / `playwright` / `fake`).
        preconditions: The scenario's preconditions (erase, reinstall mode, locale, deeplink, extra
            launch env/args). None applies the defaults.
        env_run: The subprocess runner for simctl, injectable for tests (iOS only).
        extra_env: Launch env merged in last — e.g. the per-device `BAJUTSU_COLLECTOR` url so the
            app reports to its own collector.
        record_video_dir: Web only — when set, the browser context records video here for the
            whole scenario (the `video` capture kind collects it). None records no video.

    Returns:
        A driver bound to the launched app, already polled until its UI has rendered.

    Raises:
        simctl.DeviceError: A simctl step failed, or a web target declares no `baseUrl`.
    """
    pre = preconditions or Preconditions()
    # The per-platform startup (iOS simctl sequence, web browser context, …) lives behind the
    # `Environment` seam, so this path no longer branches on the actuator name (BE-0009 Phase 0).
    environment: RunEnvironment = environment_for(actuator, udid, env_run)
    driver = environment.start(eff, pre, extra_env=extra_env, record_video_dir=record_video_dir)
    _await_ready(driver, ready_sel=eff.ready_when)
    return driver
