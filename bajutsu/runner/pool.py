"""The device pool.

Lease a device per scenario (a single-device run is a pool of one). The per-platform lease shape —
relaunch, device control, network observation, teardown — comes from the `Environment` seam, so the
pool never branches on the actuator name (BE-0009 Phase 0).
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from pathlib import Path

from bajutsu import simctl
from bajutsu.backends import (
    default_available,
    resolve_evidence_providers,
    select_actuator,
    select_actuator_for_scenario,
)
from bajutsu.backends import make_driver as _make_driver
from bajutsu.config import Effective
from bajutsu.drivers import base
from bajutsu.evidence import FileSink
from bajutsu.evidence.network import Collector, NetworkCollector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.orchestrator.evidence_rules import requested_intervals

# `device_control` / `device_relauncher` live with the platform lifecycle now; re-exported so
# `from bajutsu.runner import device_control, device_relauncher` keeps its import unchanged.
from bajutsu.platform_lifecycle import (
    ProvisionProfile,
    RunEnvironment,
    WarmRunner,
    WarmRunnerEnvironment,
    device_control,
    device_relauncher,
    environment_for,
)
from bajutsu.report import git_revision, run_provenance
from bajutsu.runner.launch import launch_driver
from bajutsu.runner.types import Lease, LeaseFn
from bajutsu.scenario import Scenario, dump_scenario_file, redact_totp_secrets
from bajutsu.webview import WebViewBridge

__all__ = ["device_control", "device_pool", "device_relauncher"]

_logger = logging.getLogger(__name__)


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
    provision: ProvisionProfile | None = None,
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
    same whether `workers` is 1 or N. The shared state is the thread-safe free-device queue, the
    read-only collectors map, and the per-device warm-runner cache (BE-XXXX) — the last is mutable,
    but each udid is touched by only the one lease that holds it (the free queue serializes that), so
    leases still need no lock.

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
        provision: The device provider's readiness report (BE-0236) — a cloud device handed over
            already booted / with the app installed lets the environment skip that setup. None (the
            local provider's inert profile) runs the full per-platform bring-up, unchanged.
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
    # Actuator selection is now per scenario (BE-0240): `lease()` resolves the cheapest actuator each
    # scenario can run on. Pool-level facts (device catalog, network-observation strategy, the
    # pre-started collectors) are *platform*-level — identical for every actuator on the platform (idb
    # and XCUITest share the simctl Simulator lifecycle) — so a representative `pool_actuator`
    # (availability-only, no scenario) reads them off one environment; nothing below branches on the
    # actuator name.
    pool_actuator = select_actuator(backends, available)
    pool_env: RunEnvironment = environment_for(
        pool_actuator, udids[0] if udids else "", env_run, provision=provision
    )
    # Resolve the device model / OS once up front (static per device) so each result can name the
    # simulator it ran on in the report; best-effort, so a missing catalog just omits it. A
    # driver-observed platform (web) has no device catalog.
    catalog = pool_env.device_catalog()
    # Resolve the git revision once (a subprocess) and reuse it across leases; the per-scenario
    # BE-0049 provenance stamp below folds it in so a first-wait timeout diagnostic is self-contained
    # (BE-0231 Unit 1). The full run manifest still recomputes the same stamp post-run.
    git_rev = git_revision()
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

    # One resident runner kept warm per device across leases (BE-XXXX), keyed by udid — a device holds
    # exactly one actuator at a time (DESIGN §3.3), and the cached handle records its actuator so a
    # lease resolving to a different one tears the old runner down first. Only the XCUITest
    # environment produces a handle; every other backend yields None here and the map stays empty, so
    # the reuse is confined to XCUITest and every other platform is byte-identical. Each udid is held
    # by one lease at a time (the free queue), so no two threads touch the same entry concurrently.
    warm_runners: dict[str, WarmRunner] = {}

    def lease(eff: Effective, scenario: Scenario) -> Lease:
        udid = free.get()
        # Resolve the actuator for *this* scenario — the cheapest one its own steps can run on
        # (BE-0240). The single-actuator-per-device rule (DESIGN §3.3/§5) is unchanged; its unit
        # narrows from "one CLI invocation" to "one scenario execution" — still exactly one actuator
        # on the leased device at any instant, never a mid-scenario swap.
        actuator = select_actuator_for_scenario(backends, scenario, available)
        lease_env: RunEnvironment = environment_for(actuator, udid, env_run, provision=provision)
        # Whether this lease's env keeps a warm runner (BE-XXXX): only XCUITest does. Pure isinstance
        # (cannot raise), so the except below can always read it; the actual adopt-or-evict runs
        # inside the try so a health-check/terminate failure still frees the device.
        warm_env = lease_env if isinstance(lease_env, WarmRunnerEnvironment) else None
        # A same-platform, read-only provider for an evidence kind this actuator can't supply
        # (BE-0020), resolved per scenario now that the actuator is. Today `network` is covered by web
        # (native) and both iOS actuators (the app-side `BAJUTSU_COLLECTOR`), so this resolves to
        # nothing in production; it activates when a platform gains a network-native actuator.
        providers, _skipped = evidence_providers(backends, actuator, available)
        network_provider = providers.get("network") if network else None
        # The collector to stop on release (the web page hook, or a BE-0020 fallback) — not the
        # pre-started HTTP receivers, which are reused and stopped in shutdown(). Released on a setup
        # failure too, so one launch failure neither leaks a socket nor starves later leases.
        release_collector: Collector | None = None

        # Teardown for the device-side collector bridge (Android's `adb reverse`, BE-0283); a no-op on
        # platforms that need none. Released on failure too, so a failed launch never leaks a tunnel.
        def release_bridge() -> None:
            pass

        try:
            # Adopt this device's warm runner (or evict a stale one) inside the try, so any
            # `alive()` / `terminate()` failure still returns udid to the free queue via the except
            # below rather than starving the device (BE-XXXX). A cached runner is reused only when it
            # matches the resolved actuator and still answers its health check; an actuator switch
            # (Unit 3) or a dead runner (Unit 4) is torn down here — before the new env starts — and
            # treated as a cache miss. `warm_env` is a pure isinstance check above, so the except can
            # always read it.
            if warm_env is not None:
                cached = warm_runners.get(udid)
                if cached is not None:
                    if cached.actuator != actuator:
                        cached.terminate()  # actuator switch (Unit 3) — expected, no warning
                        warm_runners.pop(udid, None)
                        cached = None
                    elif not cached.alive():
                        # A wedged/dead runner degrades this device back to a cold start; log it so a
                        # run that silently loses its amortization leaves a trace (Unit 4).
                        _logger.warning(
                            "warm XCUITest runner for %s failed its health check; "
                            "discarding it and cold-starting a fresh one",
                            udid,
                        )
                        cached.terminate()
                        warm_runners.pop(udid, None)
                        cached = None
                warm_env.adopt_runner(cached)
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
                # Make the host collector reachable from the leased device before launch (Android
                # tunnels the port with `adb reverse`; iOS shares the loopback and no-ops) — BE-0283.
                release_bridge = lease_env.bridge_collector(collector.port)
            webview_bridge, webview_port = _alloc_webview_bridge(lease_env)
            if webview_port is not None:
                extra_env["BAJUTSU_WEBVIEW_PORT"] = str(webview_port)
            driver, readiness = launch_driver(
                udid,
                eff,
                actuator,
                scenario.preconditions,
                env_run,
                extra_env,
                record_video_dir,
                # Start on the same environment we tear down: a stateful backend (XCUITest's resident
                # runner) must be terminated by the instance that spawned it (BE-0240).
                environment=lease_env,
                permissions=scenario.permissions,
            )
            # Cache this device's resident runner so the next same-actuator lease adopts it (BE-XXXX).
            # On reuse this re-stores the same handle; a non-reusable lease (a real device) yields
            # None, so no stale entry lingers. `release()` deliberately never terminates it — the pool
            # owns its lifetime now (shutdown / actuator switch / fault), not the lease (Unit 3).
            if warm_env is not None:
                handle = warm_env.running_handle()
                if handle is not None:
                    warm_runners[udid] = handle
                else:
                    warm_runners.pop(udid, None)
            sink = FileSink(
                run_dir,
                udid=udid,
                log_predicate=log_predicate,
                log_subsystem=log_subsystem,
                redact=eff.redact,
                secrets=secret_values,
                # A web or Android lane supplies its own interval evidence (Playwright console / page
                # errors; adb screenrecord / logcat); idb has no such method, so this is None there
                # and the simctl path is used.
                driver_interval=getattr(driver, "driver_interval", None),
                # Carried so a first-wait timeout diagnostic can state whether the readiness gate had
                # passed and on which signal, stamped with this scenario's BE-0049 provenance so the
                # evidence survives a rerun-to-green (BE-0231 Unit 1). The `scenarioHash` here
                # fingerprints this one scenario, without the file-level `description` the run
                # manifest's hash folds in when present, so it can diverge from the manifest's hash
                # even for a single-scenario run (see docs/evidence.md).
                readiness=readiness,
                provenance=run_provenance(
                    dump_scenario_file([redact_totp_secrets(scenario)]), git_revision=git_rev
                ),
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
                release_bridge()  # tear the device-side collector tunnel down first (BE-0283)
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
            release_bridge()  # a failed launch must not leak the collector tunnel (BE-0283)
            if release_collector is not None:
                release_collector.stop()
            # A fault forces a fresh runner (BE-XXXX Unit 3): terminate any runner this lease held —
            # an adopted one now cached, or a fresh spawn that never got cached — so the next lease
            # does not adopt a runner left in an unknown state.
            if warm_env is not None:
                stale = warm_runners.pop(udid, None) or warm_env.running_handle()
                if stale is not None:
                    # Best-effort: this handler's job is to never let cleanup starve the pool, so a
                    # terminate failure must not stop `free.put(udid)` below or mask the original fault.
                    try:
                        stale.terminate()
                    except Exception:
                        _logger.warning(
                            "failed to terminate warm runner for %s during fault cleanup",
                            udid,
                            exc_info=True,
                        )
            free.put(udid)
            raise

    def shutdown() -> None:
        # Run-set end (BE-XXXX Unit 3): terminate every device's warm runner. No lease is in flight
        # here, so the map is quiescent. Isolate each terminate — one wedged runner must not abort the
        # loop and leak the rest, nor skip the collector stops below.
        for handle in warm_runners.values():
            try:
                handle.terminate()
            except Exception:
                _logger.warning("failed to terminate a warm runner at shutdown", exc_info=True)
        warm_runners.clear()
        for collector in collectors.values():
            collector.stop()

    return lease, shutdown
