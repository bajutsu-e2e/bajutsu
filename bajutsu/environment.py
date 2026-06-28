"""Per-platform app lifecycle behind one Protocol (BE-0009 Phase 0).

The deterministic core never names a platform; only three seams are platform-specific — the
actuator (`drivers/*.py`), the **environment** (bring the app to a fresh, launched state), and the
stable-id convention. This module owns the second: an `Environment` Protocol whose `start` runs one
platform's whole per-run startup sequence and returns a ready-to-poll driver. The runner drives
every platform through that single call instead of branching on the actuator name — the iOS
(`simctl`) sequence and the web (browser-context) sequence live behind the same interface, and a
future Android (`adb`) environment ([BE-0007]) slots in the same way.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from bajutsu import env
from bajutsu.backends import make_driver
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.scenario import Preconditions


@runtime_checkable
class Environment(Protocol):
    """One platform's app lifecycle: produce a freshly-launched app and its driver.

    `start` owns the entire per-run startup for a platform, so the caller need not know whether that
    means a `simctl` device sequence or a fresh browser context — it gets back a driver bound to the
    launched app (not yet polled for readiness; the runner does that).
    """

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver: ...


class IosEnvironment:
    """The iOS Simulator lifecycle via `simctl` (the idb backend's environment).

    `erase` needs a shut-down device, so an erase run shuts down first (shutdown → erase → boot);
    any `simctl` step that fails is surfaced as a clean `env.DeviceError` so the CLI exits 2 instead
    of dumping a traceback.
    """

    def __init__(self, actuator: str, udid: str, env_run: env.RunFn = env._real_run) -> None:
        self._actuator = actuator
        self._udid = udid
        self._run = env_run

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        e = env.Env(self._udid, run=self._run)
        try:
            if pre.erase:
                e.shutdown()  # erase only works on a shut-down device
                e.erase()
            e.boot()
            # A configured .app is reinstalled before each run so every scenario starts from a
            # known-good binary. `clean` (default) uninstalls first (fresh app + data); `overwrite`
            # installs over the existing app. After an `erase` the app is gone, so skip the uninstall.
            if eff.app_path:
                if not Path(eff.app_path).exists():
                    raise env.DeviceError(
                        f"appPath not found: {eff.app_path} (build the app first)"
                    )
                if pre.reinstall == "clean" and not pre.erase:
                    e.uninstall(eff.bundle_id)
                e.install(eff.app_path)
            e.terminate(eff.bundle_id)  # clean start so readiness reflects the new launch
            launch_env: Mapping[str, str] = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
            }
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
        return make_driver(self._actuator, self._udid)


class WebEnvironment:
    """The web (Playwright) lifecycle: a fresh browser context is the clean state and `navigate()`
    is the launch. There is no device to erase/boot/install, so the sequence is just build + navigate.
    """

    def __init__(self, actuator: str) -> None:
        self._actuator = actuator

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        if not eff.base_url:
            raise env.DeviceError("web backend requires baseUrl (set apps.<app>.baseUrl)")
        driver = make_driver(
            self._actuator,
            "",
            base_url=eff.base_url,
            headless=eff.headless,
            record_video_dir=record_video_dir,
        )
        driver.navigate()  # type: ignore[attr-defined]  # web-only lifecycle, confined to this env
        return driver


class FakeEnvironment:
    """The test/headless backend: no lifecycle, just the fake driver."""

    def __init__(self, actuator: str, udid: str) -> None:
        self._actuator = actuator
        self._udid = udid

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        return make_driver(self._actuator, self._udid)


def environment_for(actuator: str, udid: str, env_run: env.RunFn = env._real_run) -> Environment:
    """The `Environment` for *actuator* — the seam that ends per-actuator branching in the runner."""
    if actuator == "playwright":
        return WebEnvironment(actuator)
    if actuator == "fake":
        return FakeEnvironment(actuator, udid)
    return IosEnvironment(actuator, udid, env_run)
