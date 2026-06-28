"""The device pool.

Lease a device per scenario (a single-device run is a pool of one), and the per-device relaunch /
device-control bound to a leased udid.
"""

from __future__ import annotations

import queue
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from bajutsu import env
from bajutsu.backends import default_available, resolve_evidence_providers, select_actuator
from bajutsu.backends import make_driver as _make_driver
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import FileSink
from bajutsu.network import Collector, NetworkCollector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.orchestrator.evidence_rules import requested_intervals
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
    make_driver: Callable[..., base.Driver] = _make_driver,
    evidence_providers: Callable[
        [list[str], str, Callable[[str], bool]], tuple[dict[str, str], dict[str, str]]
    ] = resolve_evidence_providers,
) -> tuple[LeaseFn, Callable[[], None]]:
    """A pool of N≥1 devices for (parallel) runs.

    `lease(eff, scenario)` leases a free udid (blocking until one frees up), launches the app
    pointed at that device's own network collector, and returns a `Lease` whose evidence sink
    (interval recordings under `run_dir`), relaunch, and device control are all bound to the leased
    device; `Lease.release()` terminates the app and returns the udid to the pool. A single-device
    run is just a pool of one, so network collection / interval evidence / device control work the
    same whether `workers` is 1 or N. The only shared state is the thread-safe free-device queue and
    the read-only collectors map, so leases need no lock.

    Args:
        udids: The devices to pool; the web backend ignores these (one browser lane).
        backends: Requested platforms/actuators; the first available one is selected.
        eff: The resolved target config.
        run_dir: Where each lease's interval evidence is written.
        network: Observe network traffic — iOS starts one HTTP collector per device up front; web
            hooks the page per lease.
        log_predicate: An `os_log` predicate scoping captured device logs. None captures none.
        log_subsystem: The app log subsystem to capture. None captures none.
        secret_values: Raw secret values to redact from evidence.
        available: Actuator-availability probe, injectable for tests.
        env_run: The subprocess runner for simctl, injectable for tests.
        make_driver: Builds a backend's driver; injectable so a test can supply a read-only
            evidence provider's driver (BE-0020).
        evidence_providers: Resolves the read-only evidence provider per gap kind (BE-0020),
            injectable for tests; defaults to the same-platform resolver.

    Returns:
        A `(lease, shutdown)` pair: `lease` leases a device for one scenario; `shutdown` stops every
        device's collector.
    """
    actuator = select_actuator(backends, available)
    is_web = actuator == "playwright"
    # A same-platform, read-only provider for an evidence kind the actuator can't supply (BE-0020).
    # Today `network` is covered by web (native) and idb (its app-side `BAJUTSU_COLLECTOR`), so this
    # resolves to nothing in production; it activates when a platform gains a second, network-native
    # actuator (e.g. iOS + XCUITest, BE-0019), at which point its collector supplies the fallback.
    providers, _skipped = evidence_providers(backends, actuator, available)
    network_provider = providers.get("network") if network else None
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
        # The fallback provider's collector and the leased udid must be released if setup raises
        # below (before a Lease — whose release() would do this — is handed out), so a single
        # launch failure neither leaks a listening socket nor starves later leases.
        fallback_collector: Collector | None = None
        try:
            # Web films the whole scenario only when its capture policy asks for video: Playwright
            # records at context-creation time, so the recording dir must be set before the driver
            # is built. iOS records on demand via simctl, so it needs no up-front dir.
            record_video_dir: Path | None = None
            if is_web and "video" in requested_intervals(scenario):
                record_video_dir = run_dir / "_video_tmp"
                record_video_dir.mkdir(parents=True, exist_ok=True)
            # iOS points the app at its pre-started HTTP collector via launch env; web has no such
            # env (Playwright observes natively), so its collector is built from the live page below.
            # A read-only fallback provider (BE-0020), when resolved, supplies the collector instead —
            # its own driver observes the same app, so the actuator's app-collector env is not used.
            collector: Collector | None
            collector_provider = "collector"
            if not is_web and network_provider is not None:
                fallback_collector = make_driver(network_provider, udid).network_collector()  # type: ignore[attr-defined]
                collector = fallback_collector
                collector_provider = f"{network_provider} (fallback)"
            else:
                collector = collectors.get(udid)
            extra_env = (
                {"BAJUTSU_COLLECTOR": f"http://127.0.0.1:{collector.port}"}
                if isinstance(collector, NetworkCollector)
                else None
            )
            driver = launch_driver(
                udid, eff, actuator, scenario.preconditions, env_run, extra_env, record_video_dir
            )
            sink = FileSink(
                run_dir,
                udid=udid,
                log_predicate=log_predicate,
                log_subsystem=log_subsystem,
                redact=eff.redact,
                secrets=secret_values,
                # On a web lane, interval evidence is Playwright-native (console / page errors), not
                # simctl; idb has no such method, so this is None there and the simctl path is used.
                web_interval=getattr(driver, "web_interval", None),
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
                if web_collector is not None:
                    collector_provider = (
                        "playwright"  # native observation (was mislabelled "collector")
                    )
                # No simctl device control / app terminate; the driver owns the browser, so a
                # release tears it down (a re-lease then builds a fresh context = clean state).
                relaunch = _web_relauncher(driver, ready_sel=eff.ready_when)
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
                    if fallback_collector is not None:
                        fallback_collector.stop()  # the read-only provider's collector (BE-0020)
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
                collector_provider=collector_provider,
            )
        except BaseException:
            if fallback_collector is not None:
                fallback_collector.stop()
            free.put(udid)
            raise

    def shutdown() -> None:
        for collector in collectors.values():
            collector.stop()

    return lease, shutdown


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
