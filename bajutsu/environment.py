"""Per-platform app lifecycle behind one Protocol (BE-0009 Phase 0).

The deterministic core never names a platform; only three seams are platform-specific — the
actuator (`drivers/*.py`), the **environment** (bring the app to a fresh, launched state), and the
stable-id convention. This module owns the second: an `Environment` Protocol whose `start` runs one
platform's whole per-run startup sequence and returns a ready-to-poll driver, and whose lease-shaping
methods (`relauncher` / `controller` / `teardown` / the network-observation strategy) let the runner
drive every platform through one interface instead of branching on the actuator name. The iOS
(`simctl`) sequence and the web (browser-context) sequence live behind the same interface, and a
future Android (`adb`) environment ([BE-0007]) slots in the same way.
"""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from bajutsu import env
from bajutsu.backends import make_driver
from bajutsu.config import Effective
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.drivers import base
from bajutsu.network import Collector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.scenario import Preconditions, Relaunch, Scenario

# Given a scenario + its launched driver, yields that scenario's `relaunch` function (defined here
# rather than imported from runner.types to keep the environment seam free of a runner import cycle).
RelaunchFactory = Callable[[Effective, Scenario, base.Driver], RelaunchFn]

# A readyWhen selector is a usable readiness signal only if it carries a per-element condition;
# positional-only fields (`index`, `within`) match every element via find_all, so they fall back to
# the element-count heuristic rather than declaring the app ready on the first element.
_READY_MATCH_KEYS = ("id", "idMatches", "label", "labelMatches", "traits", "value")


@runtime_checkable
class Environment(Protocol):
    """One platform's app lifecycle: produce a freshly-launched app and drive its per-lease shape.

    `start` owns the entire per-run startup for a platform, so the caller need not know whether that
    means a `simctl` device sequence or a fresh browser context — it gets back a driver bound to the
    launched app (not yet polled for readiness; the runner does that). The remaining methods describe
    the differences the pool used to branch on the actuator name for: how network is observed, whether
    video must be wired before launch, and the per-scenario relaunch / device control / teardown.
    """

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver: ...

    def device_catalog(self) -> dict[str, dict[str, str]]:
        """Static device metadata (model / OS) keyed by udid; `{}` for platforms with no device."""

    def observes_network_via_driver(self) -> bool:
        """Whether network is observed by hooking the live driver (web) rather than an external
        receiver the app reports to (the device backends)."""

    def records_video_up_front(self) -> bool:
        """Whether video capture must be wired before launch (web's context records at creation)
        rather than on demand (simctl)."""

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        """The page-hooked collector for a driver-observed platform, with this scenario's mocks
        wired in. Only called when `observes_network_via_driver()`."""

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        """The scenario's `relaunch` function (app restart on a device; re-navigate on web)."""

    def controller(self, eff: Effective) -> DeviceControl | None:
        """Device control for the leased device, or `None` on a platform without one (web)."""

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        """Per-release app teardown: terminate the app (device) or close the browser (web)."""

    # --- The crawl's lane shape and recovery, which the CLI used to branch on the actuator for --- #

    def has_devices(self) -> bool:
        """Whether this platform drives real devices (web has none). Sizes the crawl's lane-prep
        message and distinguishes the web browser-lane sizing from a device pool."""

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        """The crawl's lane udids. A device pool resolves *udid_arg* and caps to *workers*; web has no
        device, so *workers* alone sizes the browser-lane set (each lane one browser)."""

    def crawl_reset(self, eff: Effective) -> Reset:
        """A crawl `reset` to a clean start on this lane: relaunch the app (device) or open a fresh
        browser context (web), then wait until the first screen renders."""

    def crawl_aliveness(self) -> AliveCheck | None:
        """The crawl's crash signal for a driver-observed platform (web reads pageerror / HTTP status
        / blank DOM), or None for the device backends (the engine reads the accessibility tree)."""

    def crawl_recover(self) -> Recover | None:
        """Heal a wedged lane (relaunch a crashed/hung browser) on web, or None where the platform
        has no in-lane recovery (the device backends)."""

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        """Report blocking dialogs auto-cleared this step (web JS dialogs the driver dismisses), or
        None on platforms with no such auto-clear."""


class _DeviceEnvironment:
    """The device-style lifecycle: the iOS Simulator (`simctl`) backend and the fake test backend,
    which mimics the same shape without a real device.

    Only `start` differs between them — the fake runs no device sequence — so every lease-shaping
    method (catalog, relaunch, control, teardown, the external-receiver network strategy) lives here.
    """

    def __init__(self, actuator: str, udid: str, env_run: env.RunFn = env._real_run) -> None:
        self._actuator = actuator
        self._udid = udid
        self._run = env_run

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return env.device_catalog(self._run)

    def observes_network_via_driver(self) -> bool:
        return False  # the app reports to an external collector via BAJUTSU_COLLECTOR

    def records_video_up_front(self) -> bool:
        return False  # simctl records on demand

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        raise NotImplementedError("device backends observe network via an external receiver")

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        return device_relauncher(self._udid, self._run, extra_env)(eff, scenario, driver)

    def controller(self, eff: Effective) -> DeviceControl | None:
        return device_control(self._udid, eff.bundle_id, self._run)

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        env.Env(self._udid, run=self._run).terminate(eff.bundle_id)

    def has_devices(self) -> bool:
        return True

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        udids = [env.resolve_udid(u.strip(), self._run) for u in udid_arg.split(",") if u.strip()]
        return udids[: max(1, min(workers, len(udids)))]

    def crawl_reset(self, eff: Effective) -> Reset:
        # Return to a clean start the way `run` reaches any state: relaunch (not a full erase) so each
        # frontier revisit stays fast; the engine then replays the shortest path from the entry.
        e = env.Env(self._udid, run=self._run)

        def reset(driver: base.Driver) -> None:
            e.terminate(eff.bundle_id)
            e.launch(
                eff.bundle_id, [*eff.launch_args, *env.locale_args(eff.locale)], eff.launch_env
            )
            _await_ready(driver, ready_sel=eff.ready_when)

        return reset

    def crawl_aliveness(self) -> AliveCheck | None:
        return None  # the engine reads the accessibility tree for device crash detection

    def crawl_recover(self) -> Recover | None:
        return None  # no in-lane recovery: a wedged device surfaces as a DeviceError

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        return None  # OS prompts are handled by the optional alert guard, wired by the CLI


class IosEnvironment(_DeviceEnvironment):
    """The iOS Simulator lifecycle via `simctl` (the idb backend's environment).

    `erase` needs a shut-down device, so an erase run shuts down first (shutdown → erase → boot);
    any `simctl` step that fails is surfaced as a clean `env.DeviceError` so the CLI exits 2 instead
    of dumping a traceback.
    """

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
    is the launch. There is no device to erase/boot/install, so the sequence is just build + navigate;
    network is observed by hooking the live page, video is recorded at context creation, and a release
    closes the browser (no simctl device control).
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
            browser=eff.browser,
            record_video_dir=record_video_dir,
        )
        driver.navigate()  # type: ignore[attr-defined]  # web-only lifecycle, confined to this env
        return driver

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return {}  # no simctl device behind a browser lane

    def observes_network_via_driver(self) -> bool:
        return True  # Playwright observes the live page natively

    def records_video_up_front(self) -> bool:
        return True  # Playwright records at context-creation, before the scenario runs

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        from bajutsu.drivers.playwright import PlaywrightDriver

        # A fresh context per lease scopes the traffic; the cast names the web-only collector whose
        # `mocks` param the base Protocol widens to `list[object]`.
        return cast(PlaywrightDriver, driver).network_collector(scenario.mocks)

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        return _web_relauncher(driver, ready_sel=eff.ready_when)

    def controller(self, eff: Effective) -> DeviceControl | None:
        return None  # the driver owns the browser; no simctl device control

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        driver.close()  # type: ignore[attr-defined]  # web-only lifecycle, confined to this env

    def has_devices(self) -> bool:
        return False  # one browser per lane, no simctl device behind it

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        # No device to resolve (resolving "booted" would shell out to simctl and crash off-macOS);
        # the worker count alone sizes the browser lanes, each entry just one more browser.
        return ["web"] * max(1, workers)

    def crawl_reset(self, eff: Effective) -> Reset:
        # A fresh BrowserContext is the clean start (the `erase` equivalent): no cookies / storage
        # carried across visits, so a path recorded in one worker's browser replays in another.
        def reset(driver: base.Driver) -> None:
            driver.reset_context()  # type: ignore[attr-defined]  # web-only lifecycle (fresh context)
            _await_ready(driver, ready_sel=eff.ready_when)

        return reset

    def crawl_aliveness(self) -> AliveCheck | None:
        from bajutsu.drivers.playwright import PlaywrightDriver, web_is_alive

        # Each web worker owns its browser, so the health signal reads the worker's own driver — not
        # the primary — which is essential once `--workers` > 1 (BE-0077).
        def is_alive(driver: base.Driver, elements: list[base.Element]) -> bool:
            return web_is_alive(cast(PlaywrightDriver, driver), elements)

        return is_alive

    def crawl_recover(self) -> Recover | None:
        from bajutsu.drivers.playwright import PlaywrightDriver

        # A wedged browser (renderer crash, hung page, navigation timeout) surfaces as a DeviceError;
        # relaunch this worker's own browser so its lane heals and keeps crawling (BE-0077).
        def recover(driver: base.Driver) -> None:
            cast(PlaywrightDriver, driver).relaunch()

        return recover

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        from bajutsu.drivers.playwright import PlaywrightDriver

        # JS dialogs are auto-dismissed by the driver the moment they appear (they would otherwise
        # block the page); here we just report what was handled, for the screen map.
        def clear(driver: base.Driver) -> list[str]:
            return cast(PlaywrightDriver, driver).pop_dialogs()

        return clear


class FakeEnvironment(_DeviceEnvironment):
    """The test/headless backend: no device lifecycle, just the fake driver; otherwise device-style."""

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        return make_driver(self._actuator, self._udid)


def _allocate_port() -> int:
    """Bind an ephemeral port on localhost and return it.

    The socket is closed immediately so the runner can bind it; the window for another process to
    grab the port is negligible on localhost.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


class XcuitestEnvironment(_DeviceEnvironment):
    """The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator (BE-0019).

    The simctl sequence (erase / boot / install) is the same as idb. The difference is how the app is
    driven: instead of launching the app via simctl and actuating via idb CLI, we start an
    `xcodebuild test-without-building` subprocess that runs the BajutsuRunner XCTest target — the
    runner launches the app, starts an HTTP server on localhost, and Python drives it through the
    `XcuitestDriver` channel.
    """

    def __init__(self, actuator: str, udid: str, env_run: env.RunFn = env._real_run) -> None:
        super().__init__(actuator, udid, env_run)
        self._runner_proc: subprocess.Popen[bytes] | None = None
        self._runner_port: int = 0

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
                e.shutdown()
                e.erase()
            e.boot()
            if eff.app_path:
                if not Path(eff.app_path).exists():
                    raise env.DeviceError(
                        f"appPath not found: {eff.app_path} (build the app first)"
                    )
                if pre.reinstall == "clean" and not pre.erase:
                    e.uninstall(eff.bundle_id)
                e.install(eff.app_path)
        except subprocess.CalledProcessError as exc:
            raise env.device_error(exc) from exc

        # The runner launches the app itself (XCUIApplication.launch()), so launch_env,
        # launch_args, locale, and deeplink from preconditions are not forwarded yet — they need
        # a config channel to the runner (future slice).
        xcfg = eff.xcuitest
        if xcfg is None or xcfg.test_runner is None:
            raise env.DeviceError(
                "xcuitest backend requires xcuitest.testRunner in the target config"
            )
        runner_path = xcfg.test_runner
        if not Path(runner_path).exists():
            if xcfg.build:
                subprocess.run(shlex.split(xcfg.build), check=True)
            if not Path(runner_path).exists():
                raise env.DeviceError(f"xcuitest testRunner not found: {runner_path}")

        self._runner_port = _allocate_port()
        runner_env = {
            **os.environ,
            "BAJUTSU_RUNNER_PORT": str(self._runner_port),
            **(extra_env or {}),
        }
        self._runner_proc = subprocess.Popen(
            [  # noqa: S607 — xcodebuild resolved on PATH; requires Xcode
                "xcodebuild",
                "test-without-building",
                "-xctestrun",
                runner_path,
                "-destination",
                f"platform=iOS Simulator,id={self._udid}",
            ],
            env=runner_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        driver = make_driver(self._actuator, self._udid, runner_port=self._runner_port)
        driver.await_ready()  # type: ignore[attr-defined]  # xcuitest-only lifecycle
        return driver

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        if self._runner_proc is not None:
            self._runner_proc.terminate()
            self._runner_proc.wait(timeout=5)
            self._runner_proc = None
        super().teardown(driver, eff)


def environment_for(actuator: str, udid: str, env_run: env.RunFn = env._real_run) -> Environment:
    """The `Environment` for *actuator* — the seam that ends per-actuator branching in the runner."""
    if actuator == "playwright":
        return WebEnvironment(actuator)
    if actuator == "fake":
        return FakeEnvironment(actuator, udid, env_run)
    if actuator == "xcuitest":
        return XcuitestEnvironment(actuator, udid, env_run)
    return IosEnvironment(actuator, udid, env_run)


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


def _web_relauncher(driver: base.Driver, ready_sel: base.Selector | None = None) -> RelaunchFn:
    """Web `relaunch`: re-navigate to the base URL and wait until ready (no device restart)."""

    def relaunch(opts: Relaunch) -> None:
        driver.navigate()  # type: ignore[attr-defined]  # web-only lifecycle
        _await_ready(driver, ready_sel=ready_sel)

    return relaunch


def device_relauncher(
    udid: str, env_run: env.RunFn = env._real_run, extra_env: Mapping[str, str] | None = None
) -> RelaunchFactory:
    """A relauncher factory for the `relaunch` step.

    Restarts only the app process — terminate then launch again, re-applying the scenario's launch
    env/args plus any per-relaunch overrides, then wait until ready. The device is not erased or
    rebooted.

    Args:
        udid: The target device.
        env_run: The subprocess runner for simctl, injectable for tests.
        extra_env: Launch env re-applied across the relaunch (e.g. the device's collector url) so it
            survives; an explicit per-relaunch `env` override still wins over it.

    Returns:
        A factory that, given a scenario + driver, yields that scenario's `relaunch` function.
    """
    e = env.Env(udid, run=env_run)

    def for_scenario(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        pre = scenario.preconditions

        def relaunch(opts: Relaunch) -> None:
            e.terminate(eff.bundle_id)
            launch_env = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
                **(opts.env or {}),
            }
            locale = pre.locale or eff.locale
            launch_args = [
                *eff.launch_args,
                *pre.launch_args,
                *(opts.args or []),
                *env.locale_args(locale),
            ]
            e.launch(eff.bundle_id, launch_args, launch_env)
            _await_ready(driver, ready_sel=eff.ready_when)

        return relaunch

    return for_scenario


def device_control(udid: str, bundle_id: str, env_run: env.RunFn = env._real_run) -> DeviceControl:
    """A `DeviceControl` bound to one device.

    Backs the `setLocation` / `push` / `clearKeychain` / `clearClipboard` / `setClipboard` /
    `background` / `foreground` / `overrideStatusBar` / `clearStatusBar` steps and the `clipboard`
    assertion (read-back) via simctl.

    Args:
        udid: The target device.
        bundle_id: The app the control acts on (e.g. for `push` / `foreground`).
        env_run: The subprocess runner for simctl, injectable for tests.
    """
    e = env.Env(udid, run=env_run)

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def push(self, payload: dict[str, object]) -> None:
            e.push(bundle_id, payload)

        def clear_keychain(self) -> None:
            e.clear_keychain()

        def clear_clipboard(self) -> None:
            e.clear_clipboard()

        def set_clipboard(self, text: str) -> None:
            e.set_clipboard(text)

        def get_clipboard(self) -> str:
            return e.get_clipboard()

        def home(self) -> None:
            e.home()

        def foreground(self) -> None:
            e.foreground(bundle_id)

        def override_status_bar(self, **kwargs: str | int) -> None:
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()
