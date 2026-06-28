"""Bring a device up and launch the app: erase/boot/install/launch, then wait until ready."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from bajutsu import env
from bajutsu.backends import make_driver
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.scenario import Preconditions

# A readyWhen selector is a usable readiness signal only if it carries a per-element condition;
# positional-only fields (`index`, `within`) match every element via find_all, so they fall back to
# the element-count heuristic rather than declaring the app ready on the first element.
_READY_MATCH_KEYS = ("id", "idMatches", "label", "labelMatches", "traits", "value")


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: env.RunFn = env._real_run,
    extra_env: Mapping[str, str] | None = None,
    record_video_dir: Path | None = None,
) -> base.Driver:
    """Bring a device up, launch the app under config + scenario env, and return a ready driver.

    The iOS backend runs the simctl lifecycle (erase → boot → install → launch). simctl `erase`
    needs a shut-down device, so an erase run shuts down first (shutdown → erase → boot); any simctl
    step that fails (e.g. the app isn't installed) is surfaced as a clean `env.DeviceError` so the
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
        env.DeviceError: A simctl step failed, or a web target declares no `baseUrl`.
    """
    pre = preconditions or Preconditions()
    # Web has no device to erase/boot/install: a fresh browser context (made in the driver) is
    # the clean state, and `navigate()` is the launch. Branch out before any simctl call.
    if actuator == "playwright":
        if not eff.base_url:
            raise env.DeviceError("web backend requires baseUrl (set apps.<app>.baseUrl)")
        driver = make_driver(
            actuator,
            udid,
            base_url=eff.base_url,
            headless=eff.headless,
            record_video_dir=record_video_dir,
        )
        driver.navigate()  # type: ignore[attr-defined]  # web-only lifecycle
        _await_ready(driver, ready_sel=eff.ready_when)
        return driver
    e = env.Env(udid, run=env_run)
    try:
        if pre.erase:
            e.shutdown()  # erase only works on a shut-down device
            e.erase()
        e.boot()
        # When the app config gives a built .app, reinstall it before each run so every
        # scenario starts from a known-good binary. `reinstall=clean` (default) uninstalls
        # first (fresh app + data); `overwrite` installs over the existing app (keeps its
        # data). After an `erase` the app is already gone, so the uninstall is skipped.
        if eff.app_path:
            if not Path(eff.app_path).exists():
                raise env.DeviceError(f"appPath not found: {eff.app_path} (build the app first)")
            if pre.reinstall == "clean" and not pre.erase:
                e.uninstall(eff.bundle_id)
            e.install(eff.app_path)
        e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
        launch_env: Mapping[str, str] = {**eff.launch_env, **pre.launch_env, **(extra_env or {})}
        locale = pre.locale or eff.locale  # scenario locale overrides the app/config default
        e.launch(
            eff.bundle_id,
            [*eff.launch_args, *pre.launch_args, *env.locale_args(locale)],
            launch_env,
        )
        if pre.deeplink is not None:
            e.openurl(pre.deeplink)
    except subprocess.CalledProcessError as exc:
        raise env.device_error(exc) from exc
    driver = make_driver(actuator, udid)
    _await_ready(driver, ready_sel=eff.ready_when)
    return driver


def _await_ready(
    driver: base.Driver,
    timeout: float = 10.0,
    poll_init: float = 0.1,
    poll_max: float = 0.5,
    *,
    ready_sel: base.Selector | None = None,
) -> None:
    """Poll until the launched app has rendered its first screen.

    With `ready_sel` (a target's `readyWhen`), waits for that element to appear — the readiness
    signal for an app whose first interactive screen is a modal over always-present chrome, where the
    plain element-count heuristic would return before the modal presents. Without it, falls back to
    "more than the app root element" (any 2+ elements).

    Uses exponential backoff: the first poll is short (the app is often ready quickly) and subsequent
    intervals double up to `poll_max`, reducing wasted subprocess calls when the app takes longer to
    start.
    """
    deadline = time.monotonic() + timeout
    poll = min(poll_init, poll_max)
    # Use the selector only when it has a per-element condition; otherwise (None, empty, or
    # positional-only like `index`) fall back to the count heuristic — an all-matching selector would
    # return on a single element, weaker than "2+".
    match_sel = ready_sel if ready_sel and any(k in ready_sel for k in _READY_MATCH_KEYS) else None
    while time.monotonic() < deadline:
        try:
            elements = driver.query()
            ready = (
                len(base.find_all(elements, match_sel)) >= 1
                if match_sel is not None
                else len(elements) >= 2
            )
            if ready:
                return
        except (OSError, subprocess.CalledProcessError, ValueError):
            # The app is still coming up: a query before the UI exists can fail (no device
            # yet / empty tree / CLI hiccup). These are expected transient startup errors —
            # swallow them and keep polling until the deadline below.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll, remaining))
        poll = min(poll * 2, poll_max)
