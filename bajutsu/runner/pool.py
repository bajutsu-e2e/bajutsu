"""The device pool.

Lease a device per scenario (a single-device run is a pool of one). The per-platform lease shape —
relaunch, device control, network observation, teardown — comes from the `Environment` seam, so the
pool never branches on the actuator name (BE-0009 Phase 0).
"""

from __future__ import annotations

import queue
from collections.abc import Callable
from pathlib import Path

from bajutsu import simctl
from bajutsu.backends import default_available, resolve_evidence_providers, select_actuator
from bajutsu.backends import make_driver as _make_driver
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import FileSink
from bajutsu.network import Collector, NetworkCollector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.orchestrator.evidence_rules import requested_intervals

# `device_control` / `device_relauncher` live with the platform lifecycle now; re-exported so
# `from bajutsu.runner import device_control, device_relauncher` keeps its import unchanged.
from bajutsu.platform_lifecycle import (
    RunEnvironment,
    device_control,
    device_relauncher,
    environment_for,
)
from bajutsu.runner.launch import launch_driver
from bajutsu.runner.types import Lease, LeaseFn
from bajutsu.scenario import Scenario
from bajutsu.webview import WebViewBridge

__all__ = ["device_control", "device_pool", "device_relauncher"]


def _alloc_webview_bridge(
    lease_env: object,
) -> tuple[WebViewBridge | None, int | None]:
    """Allocate a WebView bridge for platforms that need one (iOS, not web).

    Returns (bridge, port) or (None, None) when the platform doesn't use the bridge.
    """
    if getattr(lease_env, "observes_network_via_driver", lambda: False)():
        return None, None
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return WebViewBridge(port=port), port


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
    env_run: simctl.RunFn = simctl._real_run,
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
    # The platform's whole lease shape (catalog, network strategy, relaunch, control, teardown) comes
    # from its Environment, so nothing below branches on the actuator name. Pool-level facts read off
    # a representative environment; the device-scoped parts use one built per leased udid.
    pool_env: RunEnvironment = environment_for(actuator, udids[0] if udids else "", env_run)
    # A same-platform, read-only provider for an evidence kind the actuator can't supply (BE-0020).
    # Today `network` is covered by web (native) and idb (its app-side `BAJUTSU_COLLECTOR`), so this
    # resolves to nothing in production; it activates when a platform gains a second, network-native
    # actuator (e.g. iOS + XCUITest, BE-0019), at which point its collector supplies the fallback.
    providers, _skipped = evidence_providers(backends, actuator, available)
    network_provider = providers.get("network") if network else None
    # Resolve the device model / OS once up front (static per device) so each result can name the
    # simulator it ran on in the report; best-effort, so a missing catalog just omits it. A
    # driver-observed platform (web) has no device catalog.
    catalog = pool_env.device_catalog()
    free: queue.Queue[str] = queue.Queue()
    for udid in udids:
        free.put(udid)
    # One collector per device (its own ephemeral port), started up front and reused across leases
    # (cleared per scenario by the run loop). If a start fails mid-setup, stop the ones already
    # started so we don't leak listening sockets. Only the external-receiver path (the device
    # backends) pre-starts these; a driver-observed platform (web) has no up-front receiver and hooks
    # its collector to the page built per lease instead.
    collectors: dict[str, NetworkCollector] = {}
    if network and not pool_env.observes_network_via_driver():
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
        lease_env: RunEnvironment = environment_for(actuator, udid, env_run)
        # The collector to stop on release (the web page hook, or a BE-0020 fallback) — not the
        # pre-started HTTP receivers, which are reused and stopped in shutdown(). Released on a setup
        # failure too, so one launch failure neither leaks a socket nor starves later leases.
        release_collector: Collector | None = None
        try:
            # Web films the whole scenario only when its capture policy asks for video: Playwright
            # records at context-creation time, so the recording dir must be set before the driver
            # is built. A device backend records on demand, so it needs no up-front dir.
            record_video_dir: Path | None = None
            if lease_env.records_video_up_front() and "video" in requested_intervals(scenario):
                record_video_dir = run_dir / "_video_tmp"
                record_video_dir.mkdir(parents=True, exist_ok=True)
            # A device backend points the app at its pre-started HTTP collector via launch env; a
            # driver-observed platform has no such env (it observes natively) and hooks its collector
            # from the live page after launch. A read-only fallback provider (BE-0020), when resolved,
            # supplies the collector instead — its own driver observes the same app.
            collector: Collector | None
            collector_provider = "collector"
            if not lease_env.observes_network_via_driver():
                if network_provider is not None:
                    fallback = make_driver(network_provider, udid).network_collector()  # type: ignore[attr-defined]
                    collector = release_collector = fallback
                    collector_provider = f"{network_provider} (fallback)"
                else:
                    collector = collectors.get(udid)
            else:
                collector = None  # resolved after launch from the live page
            extra_env: dict[str, str] = {}
            if isinstance(collector, NetworkCollector):
                extra_env["BAJUTSU_COLLECTOR"] = f"http://127.0.0.1:{collector.port}"
                extra_env["BAJUTSU_COLLECTOR_TOKEN"] = collector.token
            webview_bridge, webview_port = _alloc_webview_bridge(lease_env)
            if webview_port is not None:
                extra_env["BAJUTSU_WEBVIEW_PORT"] = str(webview_port)
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
            # A driver-observed platform hooks its collector to the live page now (and fulfils this
            # scenario's mocks); a fresh context per lease scopes its traffic, mirroring the device's
            # per-scenario collector clear. It is stopped on release.
            if lease_env.observes_network_via_driver() and network:
                collector = release_collector = lease_env.hook_collector(driver, scenario)
                # Native observation by the selected actuator, not the app-side receiver. Naming the
                # actuator keeps provenance accurate if another driver-observed actuator is added;
                # today this is "playwright".
                collector_provider = actuator
            relaunch: RelaunchFn = lease_env.relauncher(eff, scenario, driver, extra_env=extra_env)
            control: DeviceControl | None = lease_env.controller(eff)

            def release() -> None:
                if release_collector is not None:
                    release_collector.stop()
                lease_env.teardown(driver, eff)
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
                webview_bridge=webview_bridge,
            )
        except BaseException:
            if release_collector is not None:
                release_collector.stop()
            free.put(udid)
            raise

    def shutdown() -> None:
        for collector in collectors.values():
            collector.stop()

    return lease, shutdown
