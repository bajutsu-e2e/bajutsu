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


def launch_driver(
    udid: str,
    eff: Effective,
    actuator: str,
    preconditions: Preconditions | None = None,
    env_run: env.RunFn = env._real_run,
    extra_env: Mapping[str, str] | None = None,
) -> base.Driver:
    """Erase/boot/launch the app (with config + scenario env) and return a driver.

    simctl `erase` requires a shut-down device, so an erase run shuts the device
    down first (shutdown -> erase -> boot); otherwise erasing a booted Simulator
    fails. Any simctl step that still fails (e.g. the app isn't installed) is
    surfaced as a clean env.DeviceError so the CLI can exit 2 instead of dumping a
    traceback.

    `extra_env` is merged last into the launch env (e.g. the per-device
    `BAJUTSU_COLLECTOR` url so the app reports to its own collector).
    """
    pre = preconditions or Preconditions()
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
    _await_ready(driver)
    return driver


def _await_ready(
    driver: base.Driver,
    timeout: float = 10.0,
    poll_init: float = 0.1,
    poll_max: float = 0.5,
) -> None:
    """Poll until the launched app has rendered a UI (more than the app root element).

    Uses exponential backoff: the first poll is short (the app is often ready quickly)
    and subsequent intervals double up to `poll_max`, reducing wasted subprocess calls
    when the app takes longer to start."""
    deadline = time.monotonic() + timeout
    poll = min(poll_init, poll_max)
    while time.monotonic() < deadline:
        try:
            if len(driver.query()) >= 2:
                return
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll, remaining))
        poll = min(poll * 2, poll_max)
