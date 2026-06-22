"""The device pool: lease a device per scenario (a single-device run is a pool of one), and the
per-device relaunch / device-control bound to a leased udid."""

from __future__ import annotations

import queue
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from bajutsu import env
from bajutsu.backends import default_available, select_actuator
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import FileSink
from bajutsu.network import Collector, NetworkCollector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.runner.launch import _await_ready, launch_driver
from bajutsu.runner.types import Lease, LeaseFn, RelaunchFactory
from bajutsu.scenario import Relaunch, Scenario


def device_pool(
    udids: list[str],
    backends: list[str],
    eff: Effective,
    run_dir: Path,
    *,
    network: bool = False,
    log_predicate: str | None = None,
    log_subsystem: str | None = None,
    secret_values: list[str] | None = None,
    available: Callable[[str], bool] = default_available,
    env_run: env.RunFn = env._real_run,
) -> tuple[LeaseFn, Callable[[], None]]:
    """A pool of N>=1 devices for (parallel) runs.

    `lease(eff, scenario)` leases a free udid (blocking until one frees up), launches the app
    pointed at that device's own network collector, and returns a Lease whose evidence sink
    (interval recordings under `run_dir`), relaunch, and device control are all bound to the
    leased device. The Lease's `release()` terminates the app and returns the udid to the
    pool. `shutdown()` stops every device's collector.

    A single-device run is just a pool of one, so network collection / interval evidence /
    device control work the same whether `workers` is 1 or N. The only shared state is the
    free-device queue (thread-safe) and the read-only collectors map, so leases need no lock.

    Returns (lease, shutdown).
    """
    actuator = select_actuator(backends, available)
    is_web = actuator == "playwright"
    # Resolve the device model / OS once up front (static per device) so each result can name
    # the simulator it ran on in the report; best-effort, so a missing catalog just omits it.
    # Web has no simctl catalog.
    catalog = {} if is_web else env.device_catalog(env_run)
    free: queue.Queue[str] = queue.Queue()
    for udid in udids:
        free.put(udid)
    # One collector per device (its own ephemeral port), started up front and reused across
    # leases (cleared per scenario by the run loop). If a start fails mid-setup, stop the
    # ones already started so we don't leak listening sockets.
    # iOS collectors are HTTP receivers started up front and reused; web has no up-front receiver
    # (its collector hooks the page built per lease), so only the non-web path pre-starts them.
    collectors: dict[str, NetworkCollector] = {}
    if network and not is_web:
        started: list[NetworkCollector] = []
        try:
            for udid in udids:
                collector = NetworkCollector()
                collector.start()
                collectors[udid] = collector
                started.append(collector)
        except Exception:
            for collector in started:
                collector.stop()
            raise

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        udid = free.get()
        # iOS points the app at its pre-started HTTP collector via launch env; web has no such
        # env (Playwright observes natively), so its collector is built from the live page below.
        collector: Collector | None = collectors.get(udid)
        extra_env = (
            {"BAJUTSU_COLLECTOR": f"http://127.0.0.1:{collector.port}"}
            if isinstance(collector, NetworkCollector)
            else None
        )
        driver = launch_driver(udid, eff, actuator, scenario.preconditions, env_run, extra_env)
        sink = FileSink(
            run_dir,
            udid=udid,
            log_predicate=log_predicate,
            log_subsystem=log_subsystem,
            redact=eff.redact,
            secrets=secret_values,
        )
        relaunch: RelaunchFn
        control: DeviceControl | None
        if is_web:
            from bajutsu.drivers.playwright import PlaywrightDriver

            # The web collector hooks the live page (and fulfills this scenario's mocks); a fresh
            # context per lease scopes its traffic, mirroring iOS's per-scenario collector clear.
            web_collector = (
                cast(PlaywrightDriver, driver).network_collector(scenario.mocks)
                if network
                else None
            )
            collector = web_collector
            # No simctl device control / app terminate; the driver owns the browser, so a
            # release tears it down (a re-lease then builds a fresh context = clean state).
            relaunch = _web_relauncher(driver)
            control = None

            def release() -> None:
                if web_collector is not None:
                    web_collector.stop()
                driver.close()  # type: ignore[attr-defined]  # web-only lifecycle
                free.put(udid)
        else:
            relaunch = device_relauncher(udid, env_run, extra_env)(eff, scenario, driver)
            control = device_control(udid, eff.bundle_id, env_run)

            def release() -> None:
                env.Env(udid, run=env_run).terminate(eff.bundle_id)
                free.put(udid)

        meta = catalog.get(udid, {})
        return Lease(
            driver=driver,
            sink=sink,
            relaunch=relaunch,
            control=control,
            collector=collector,
            release=release,
            udid=udid,
            device_name=meta.get("name", ""),
            device_runtime=meta.get("runtime", ""),
        )

    def shutdown() -> None:
        for collector in collectors.values():
            collector.stop()

    return lease, shutdown


def device_control(udid: str, bundle_id: str, env_run: env.RunFn = env._real_run) -> DeviceControl:
    """A DeviceControl bound to one device, backing `setLocation` / `push` /
    `clearKeychain` / `clearClipboard` / `background` / `overrideStatusBar` /
    `clearStatusBar` steps via simctl."""
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

        def home(self) -> None:
            e.home()

        def override_status_bar(self, **kwargs: str | int) -> None:
            e.override_status_bar(**kwargs)

        def clear_status_bar(self) -> None:
            e.clear_status_bar()

    return _Control()


def _web_relauncher(driver: base.Driver) -> RelaunchFn:
    """Web `relaunch`: re-navigate to the base URL and wait until ready (no device restart)."""

    def relaunch(opts: Relaunch) -> None:
        driver.navigate()  # type: ignore[attr-defined]  # web-only lifecycle
        _await_ready(driver)

    return relaunch


def device_relauncher(
    udid: str, env_run: env.RunFn = env._real_run, extra_env: Mapping[str, str] | None = None
) -> RelaunchFactory:
    """A relauncher for a `relaunch` step: terminate the app and launch it again (re-applying
    the scenario's launch env/args, plus any per-relaunch overrides), then wait until ready.
    The device is not erased/rebooted — only the app process restarts.

    `extra_env` (e.g. the device's collector url) is re-applied so it survives the relaunch;
    an explicit per-relaunch `env` override still wins over it.
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
            _await_ready(driver)

        return relaunch

    return for_scenario
