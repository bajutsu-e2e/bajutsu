"""Per-platform app lifecycle behind one Protocol (BE-0009 Phase 0).

The deterministic core never names a platform; only three seams are platform-specific — the
actuator (`drivers/*.py`), the **environment** (bring the app to a fresh, launched state), and the
stable-id convention. This module owns the second: an `Environment` Protocol whose `start` runs one
platform's whole per-run startup sequence and returns a ready-to-poll driver, and whose lease-shaping
methods (`relauncher` / `controller` / `teardown` / the network-observation strategy) let the runner
drive every platform through one interface instead of branching on the actuator name. The iOS
(`simctl`) sequence, the web (browser-context) sequence, and the Android (`adb`, [BE-0007]) sequence
live behind the same interface, and a further platform slots in the same way.

## Two lease surfaces (BE-0197)

The seam serves two commands, so its Protocol is split by command rather than carried as one flat
surface: `RunEnvironment` is the `run` lease (`start`, `device_catalog`, `relauncher`, `controller`,
`teardown`, `hook_collector`, and the run predicates); `CrawlEnvironment` is the `crawl` lease (`has_devices`,
`plan_lanes`, and the `crawl_*` methods). Every concrete platform implements both, and `Environment`
is their union — the full surface a platform class satisfies and `environment_for` returns. The
`run` pipeline (`runner/pool.py`, `runner/launch.py`) holds its environment as a `RunEnvironment`
and the `crawl` command (`cli/commands/crawl.py`) as a `CrawlEnvironment`, so each reader sees only
the methods its command calls and mypy keeps the two from drifting into each other.

## Declining a method (the "not applicable" contract)

A method a platform has no use for is declined in exactly one of two ways, chosen per method (never
ad hoc), and each method's docstring states which it is:

- **First-class null / empty** — for a method the caller *always* invokes and interprets a null
  answer from: `controller` → `None` (no device control), `device_catalog` → `{}` (no devices),
  `crawl_aliveness` / `crawl_recover` / `crawl_dialog_clearer` → `None` (no such behavior here).
  The null value *is* the platform's answer, not an unimplemented stub — so a declining platform
  returns it rather than raising.
- **Gated raise** — only for a method the caller invokes *solely when* a predicate is true:
  `hook_collector`, which the runner calls only after `observes_network_via_driver()`. A platform
  that returns `False` from the predicate may leave `hook_collector` raising `NotImplementedError`,
  because the check makes the raise unreachable. This is the *only* method that may raise.

## Predicate → capability pairing

Two run predicates each gate one capability method, honored at a single runner call site. A third
predicate, `has_devices`, is a `crawl`-side flag that shapes the lane-prep message — it gates
nothing (`plan_lanes` is called unconditionally):

| Predicate                     | Role                                            | Honored at                 |
|-------------------------------|-------------------------------------------------|----------------------------|
| `observes_network_via_driver` | gates `hook_collector` (may gated-raise if F)   | `runner/pool.py` (`lease`) |
| `records_video_up_front`      | gates `start`'s `record_video_dir` wiring       | `runner/pool.py` (`lease`) |
| `has_devices`                 | shapes the crawl lane-prep message (not a gate) | `cli/commands/crawl.py`    |

## Adding a platform

A new `Environment` (extend `environment_for`) must, at minimum:

1. Implement the full `RunEnvironment` surface: `start` (the per-run bring-up returning a launched
   driver), `relauncher`, `controller` (return `None` if none), `teardown`, `device_catalog`
   (return `{}` if none), and the two run predicates (`observes_network_via_driver`,
   `records_video_up_front`). `hook_collector` may gated-raise unless `observes_network_via_driver()`
   returns `True`.
2. Implement `CrawlEnvironment` as well: `has_devices`, `plan_lanes`, `crawl_reset`, and the three
   `crawl_*` health methods (return `None` from each the platform lacks). `environment_for` returns
   the union `Environment`, so a platform class must satisfy both surfaces — but the crawl half is
   cheap: the health methods are first-class `None`, and a run-first platform can mirror its
   `relauncher` in `crawl_reset` and its device pooling in `plan_lanes`. Consumers still narrow to
   the one surface they use (`RunEnvironment` in the run pipeline, `CrawlEnvironment` in `crawl`);
   the union is what a *new class* provides, not what either *reader* depends on.

Follow the "not applicable" contract above for every method the platform declines; do not invent a
third idiom.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from bajutsu import adb, simctl
from bajutsu.backends import make_driver
from bajutsu.config import Effective, require_android, require_ios, require_web
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.doctor import namespace_of
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
class RunEnvironment(Protocol):
    """The `run` lease surface: produce a freshly-launched app and drive its per-lease shape.

    `start` owns the entire per-run startup for a platform, so the caller need not know whether that
    means a `simctl` device sequence or a fresh browser context — it gets back a driver bound to the
    launched app (not yet polled for readiness; the runner does that). The remaining methods describe
    the differences the pool used to branch on the actuator name for: how network is observed, whether
    video must be wired before launch, and the per-scenario relaunch / device control / teardown.
    This is the narrower surface the `run` pipeline (`runner/pool.py`, `runner/launch.py`) holds; the
    module docstring's "not applicable" contract governs how a platform declines each method.
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
        """Static device metadata (model / OS) keyed by udid.

        Returns `{}` for a platform with no device (web) — a first-class "no devices", not an
        unimplemented stub; the caller always invokes this and reads the empty map as the answer.
        """

    def observes_network_via_driver(self) -> bool:
        """Whether network is observed by hooking the live driver (web) rather than an external
        receiver the app reports to (the device backends). Gates `hook_collector`."""

    def records_video_up_front(self) -> bool:
        """Whether video capture must be wired before launch (web's context records at creation)
        rather than on demand (simctl). Gates `start`'s `record_video_dir` handling."""

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        """The page-hooked collector for a driver-observed platform, with this scenario's mocks wired
        in.

        Gated raise: the runner calls this *only when* `observes_network_via_driver()` is `True`, so a
        platform that returns `False` there may leave this raising `NotImplementedError` — the check
        makes the raise unreachable. This is the only Protocol method permitted to raise.
        """

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
        """Device control for the leased device.

        Returns `None` on a platform without one (web) — a first-class "no device control" the runner
        interprets, not an unimplemented stub.
        """

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        """Per-release app teardown: terminate the app (device) or close the browser (web)."""


@runtime_checkable
class CrawlEnvironment(Protocol):
    """The `crawl` lease surface: the lane shape and health seams the CLI used to branch on the
    actuator for.

    This is the narrower surface the `crawl` command (`cli/commands/crawl.py`) holds; the concrete
    platform classes satisfy it alongside `RunEnvironment`. The three `crawl_*` health methods follow
    the module docstring's first-class-null contract — a platform without a given behavior returns
    `None` rather than raising.
    """

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
        / blank DOM).

        Returns `None` for the device backends (the engine reads the accessibility tree) — a
        first-class "no such signal here", not an unimplemented stub.
        """

    def crawl_recover(self) -> Recover | None:
        """Heal a wedged lane (relaunch a crashed/hung browser) on web.

        Returns `None` where the platform has no in-lane recovery (the device backends) — a
        first-class "no recovery here", not an unimplemented stub.
        """

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        """Report blocking dialogs auto-cleared this step (web JS dialogs the driver dismisses).

        Returns `None` on platforms with no such auto-clear — a first-class "nothing auto-cleared
        here", not an unimplemented stub.
        """


@runtime_checkable
class Environment(RunEnvironment, CrawlEnvironment, Protocol):
    """One platform's whole app lifecycle: the union of the `run` and `crawl` lease surfaces.

    Every concrete platform class satisfies this combined surface, and `environment_for` returns it;
    each consumer then narrows to the one it needs (`RunEnvironment` for the run pipeline,
    `CrawlEnvironment` for the crawl command). See the module docstring for the "not applicable"
    contract and the "adding a platform" checklist.
    """


class _DeviceEnvironment:
    """The device-style lifecycle: the iOS Simulator (`simctl`) backend and the fake test backend,
    which mimics the same shape without a real device.

    Only `start` differs between them — the fake runs no device sequence — so every lease-shaping
    method (catalog, relaunch, control, teardown, the external-receiver network strategy) lives here.
    """

    def __init__(self, actuator: str, udid: str, env_run: simctl.RunFn = simctl._real_run) -> None:
        self._actuator = actuator
        self._udid = udid
        self._run = env_run

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return simctl.device_catalog(self._run)

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
        return device_control(self._udid, require_ios(eff).bundle_id, self._run)

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        simctl.Env(self._udid, run=self._run).terminate(require_ios(eff).bundle_id)

    def has_devices(self) -> bool:
        return True

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        udids = [
            simctl.resolve_udid(u.strip(), self._run) for u in udid_arg.split(",") if u.strip()
        ]
        return udids[: max(1, min(workers, len(udids)))]

    def crawl_reset(self, eff: Effective) -> Reset:
        # Return to a clean start the way `run` reaches any state: relaunch (not a full erase) so each
        # frontier revisit stays fast; the engine then replays the shortest path from the entry.
        e = simctl.Env(self._udid, run=self._run)
        bundle_id = require_ios(eff).bundle_id

        def reset(driver: base.Driver) -> None:
            e.terminate(bundle_id)
            e.launch(bundle_id, [*eff.launch_args, *simctl.locale_args(eff.locale)], eff.launch_env)
            _await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

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
    any `simctl` step that fails is surfaced as a clean `simctl.DeviceError` so the CLI exits 2 instead
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
        ios = require_ios(eff)
        e = simctl.Env(self._udid, run=self._run)
        try:
            if pre.erase:
                e.shutdown()  # erase only works on a shut-down device
                e.erase()
            e.boot()
            # A configured .app is reinstalled before each run so every scenario starts from a
            # known-good binary. `clean` (default) uninstalls first (fresh app + data); `overwrite`
            # installs over the existing app. After an `erase` the app is gone, so skip the uninstall.
            if ios.app_path:
                if not Path(ios.app_path).exists():
                    raise simctl.DeviceError(
                        f"appPath not found: {ios.app_path} (build the app first)"
                    )
                if pre.reinstall == "clean" and not pre.erase:
                    e.uninstall(ios.bundle_id)
                e.install(ios.app_path)
            e.terminate(ios.bundle_id)  # clean start so readiness reflects the new launch
            launch_env: Mapping[str, str] = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
            }
            locale = pre.locale or eff.locale  # scenario locale overrides the app/config default
            e.launch(
                ios.bundle_id,
                [*eff.launch_args, *pre.launch_args, *simctl.locale_args(locale)],
                launch_env,
            )
            if pre.deeplink is not None:
                e.openurl(pre.deeplink)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc
        return make_driver(self._actuator, self._udid)


def _await_boot(env: adb.Env, timeout: float = 60.0, poll: float = 0.5) -> None:
    """Wait until the device reports `sys.boot_completed`, polling to a bounded deadline (a condition wait at `poll` intervals, not a fixed up-front sleep).

    The Android peer of `simctl bootstatus`: `getprop sys.boot_completed` is polled to a bounded
    deadline. `boot_completed` treats a device adb can't yet see as "not booted" and retries it (no
    unbounded `adb wait-for-device` block), but lets a missing `adb` binary propagate so `start`
    fails fast with a clean error instead of spinning here. An already-booted device returns on the
    first poll; if the deadline passes with no device, the launch sequence proceeds and fails loudly
    on the first `pm clear` / `am start` with a clean `DeviceError`, rather than hanging here.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if env.boot_completed():
            return
        time.sleep(poll)


class AndroidEnvironment:
    """The Android emulator lifecycle via `adb` (the adb backend's environment) — idb's twin.

    `start` runs the adb sequence — boot-readiness wait → optional APK (re)install → `pm clear` for a
    clean state (the `erase` equivalent) → `am force-stop` → runtime-permission pre-grant (`pm grant`,
    BE-0210) → `am start` (launch env forwarded as intent extras) → deeplink — and returns the `adb`
    driver. The lease-shaping methods mirror the iOS
    `_DeviceEnvironment`, over `adb` instead of `simctl`: the same seam, a different subprocess tool.
    Network is not observed natively (no `NETWORK` capability), so that path degrades the same honest
    way iOS's mocked network does. Device control backs the subset the emulator can honor
    (`setLocation`, BE-0211, plus clipboard through the app's in-app receiver, BE-0233); the rest of
    the family stays unsupported.
    """

    def __init__(self, actuator: str, serial: str, adb_run: adb.RunFn = adb._real_run) -> None:
        self._actuator = actuator
        self._serial = serial
        self._run = adb_run

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        android = require_android(eff)
        e = adb.Env(self._serial, run=self._run)
        try:
            _await_boot(e)
            if android.app_path:
                if not Path(android.app_path).exists():
                    raise adb.DeviceError(
                        f"appPath not found: {android.app_path} (build the app first)"
                    )
                e.install(android.app_path)
            # `pm clear` is the clean-state reset (fresh app data); skip it only on an explicit
            # `overwrite` reinstall with no erase, matching iOS's "keep data" overwrite path.
            if pre.erase or pre.reinstall == "clean":
                e.clear(android.package)
            e.force_stop(android.package)  # clean start so readiness reflects the new launch
            # Grant runtime permissions after `pm clear` (which resets grants) but before launch, so
            # a permission prompt never blocks the scenario — deterministic, no timing (BE-0210).
            e.grant_permissions(android.package, android.grant_permissions)
            launch_env: Mapping[str, str] = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
            }
            e.launch(android.package, launch_env)
            if pre.deeplink is not None:
                e.open_url(pre.deeplink, android.package)
        except subprocess.CalledProcessError as exc:
            raise adb.device_error(exc) from exc
        except OSError as exc:
            # adb itself could not be run (e.g. missing from PATH) — surface it as a clean
            # DeviceError (exit 2) rather than an unhandled traceback or a spin to the boot deadline.
            raise adb.DeviceError(
                f"could not run adb ({exc}); is Android platform-tools installed and on PATH?"
            ) from exc
        return make_driver(self._actuator, self._serial)

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return adb.device_catalog(self._run)

    def observes_network_via_driver(self) -> bool:
        return False  # no native network monitor — the same mocked story as iOS

    def records_video_up_front(self) -> bool:
        return False  # screenrecord records on demand via driver_interval, not up front like web

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        raise NotImplementedError("the adb backend does not observe network via the driver")

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        package = require_android(eff).package
        e = adb.Env(self._serial, run=self._run)
        pre = scenario.preconditions

        def relaunch(opts: Relaunch) -> None:
            e.force_stop(package)  # restart only the app; the device is not rebooted
            launch_env = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
                **(opts.env or {}),
            }
            e.launch(package, launch_env)
            _await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

        return relaunch

    def controller(self, eff: Effective) -> DeviceControl | None:
        # The emulator-backed subset (setLocation over the console + clipboard over the app's in-app
        # receiver, BE-0233); the rest of the family raises UnsupportedAction, and preflight (BE-0212)
        # rejects it up front from the adb capability set. Clipboard addresses its broadcast at the
        # app under test, so the package is threaded through.
        return android_device_control(self._serial, require_android(eff).package, self._run)

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        adb.Env(self._serial, run=self._run).force_stop(require_android(eff).package)

    def has_devices(self) -> bool:
        return True

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        serials = [
            adb.resolve_serial(s.strip(), self._run) for s in udid_arg.split(",") if s.strip()
        ]
        return serials[: max(1, min(workers, len(serials)))]

    def crawl_reset(self, eff: Effective) -> Reset:
        package = require_android(eff).package
        e = adb.Env(self._serial, run=self._run)

        def reset(driver: base.Driver) -> None:
            e.force_stop(package)
            e.launch(package, eff.launch_env)
            _await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

        return reset

    def crawl_aliveness(self) -> AliveCheck | None:
        return None  # the engine reads the accessibility tree for device crash detection

    def crawl_recover(self) -> Recover | None:
        return None  # no in-lane recovery: a wedged device surfaces as a DeviceError

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        return None  # OS prompts are handled by the optional alert guard, wired by the CLI


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
        web = require_web(eff)
        if not web.base_url:
            raise simctl.DeviceError("web backend requires baseUrl (set targets.<name>.baseUrl)")
        driver = make_driver(
            self._actuator,
            "",
            base_url=web.base_url,
            headless=web.headless,
            browser=web.browser,
            device_mode=web.device_mode,
            record_video_dir=record_video_dir,
        )
        cast(base.BackendLifecycle, driver).navigate()  # web-only lifecycle, confined to this env
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
        return _web_relauncher(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

    def controller(self, eff: Effective) -> DeviceControl | None:
        return None  # the driver owns the browser; no simctl device control

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        cast(base.BackendLifecycle, driver).close()  # web-only lifecycle, confined to this env

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
            cast(base.BackendLifecycle, driver).reset_context()  # web-only (fresh context)
            _await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

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


# Cold `xcodebuild test-without-building` startup (XCTest host boot + app launch before the runner's
# server answers /health) routinely exceeds the driver's 10s default on a loaded CI runner; a warm
# start still returns as soon as /health is ready, so this only raises the ceiling for the cold case.
_RUNNER_STARTUP_TIMEOUT = 120.0


class XcuitestEnvironment(_DeviceEnvironment):
    """The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator (BE-0019).

    The simctl sequence (erase / boot / install) is the same as idb. The difference is how the app is
    driven: instead of launching the app via simctl and actuating via idb CLI, we start an
    `xcodebuild test-without-building` subprocess that runs the BajutsuRunner XCTest target — the
    runner launches the app, starts an HTTP server on localhost, and Python drives it through the
    `XcuitestDriver` channel.
    """

    def __init__(self, actuator: str, udid: str, env_run: simctl.RunFn = simctl._real_run) -> None:
        super().__init__(actuator, udid, env_run)
        self._runner_proc: subprocess.Popen[bytes] | None = None
        self._runner_port: int = 0
        self._patched_runner: Path | None = None

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
    ) -> base.Driver:
        ios = require_ios(eff)
        e = simctl.Env(self._udid, run=self._run)
        try:
            if pre.erase:
                e.shutdown()
                e.erase()
            e.boot()
            if ios.app_path:
                if not Path(ios.app_path).exists():
                    raise simctl.DeviceError(
                        f"appPath not found: {ios.app_path} (build the app first)"
                    )
                if pre.reinstall == "clean" and not pre.erase:
                    e.uninstall(ios.bundle_id)
                e.install(ios.app_path)
        except subprocess.CalledProcessError as exc:
            raise simctl.device_error(exc) from exc

        # The runner launches the app via XCUIApplication.launch(). Preconditions are forwarded
        # through env vars: the runner reads BAJUTSU_LAUNCH_ENV_* and sets them on
        # launchEnvironment, BAJUTSU_LAUNCH_ARGS as launchArguments, and opens BAJUTSU_DEEPLINK.
        launch_env: Mapping[str, str] = {
            **eff.launch_env,
            **pre.launch_env,
            **(extra_env or {}),
        }
        locale = pre.locale or eff.locale
        launch_args = [*eff.launch_args, *pre.launch_args, *simctl.locale_args(locale)]

        xcfg = ios.xcuitest
        if xcfg is None or xcfg.test_runner is None:
            raise simctl.DeviceError(
                "xcuitest backend requires xcuitest.testRunner in the target config"
            )
        runner_path = xcfg.test_runner
        if not Path(runner_path).exists():
            if xcfg.build:
                try:
                    subprocess.run(shlex.split(xcfg.build), check=True)
                except (subprocess.CalledProcessError, OSError) as exc:
                    raise simctl.DeviceError(
                        f"xcuitest build command failed: {xcfg.build}"
                    ) from exc
            if not Path(runner_path).exists():
                raise simctl.DeviceError(f"xcuitest testRunner not found: {runner_path}")

        self._runner_port = _allocate_port()
        forwarded = {
            "BAJUTSU_RUNNER_PORT": str(self._runner_port),
            # One generic runner drives whatever app the run targets, so it launches this
            # bundle id via XCUIApplication(bundleIdentifier:) rather than its own target app.
            "BAJUTSU_BUNDLE_ID": ios.bundle_id,
            **{f"BAJUTSU_LAUNCH_ENV_{k}": v for k, v in launch_env.items()},
            "BAJUTSU_LAUNCH_ARGS": json.dumps(launch_args),
        }
        if pre.deeplink is not None:
            forwarded["BAJUTSU_DEEPLINK"] = pre.deeplink

        # `xcodebuild` does not pass its own environment through to the test-runner process
        # inside the Simulator, so the runner reads these from the .xctestrun's per-target
        # TestingEnvironmentVariables instead. Patch a private copy and run that.
        self._patched_runner = _patch_xctestrun_env(Path(runner_path), forwarded)
        try:
            self._runner_proc = subprocess.Popen(
                [  # noqa: S607 — xcodebuild resolved on PATH; requires Xcode
                    "xcodebuild",
                    "test-without-building",
                    "-xctestrun",
                    str(self._patched_runner),
                    "-destination",
                    # Validate the udid inline before it lands on the xcodebuild command line
                    # (belt-and-suspenders: `simctl.Env(self._udid)` above already raised on a bad
                    # id) — the same defense-in-depth the simctl/idb argv builders apply.
                    f"platform=iOS Simulator,id={simctl.validated_udid(self._udid)}",
                ],
                env={**os.environ, **forwarded},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise simctl.DeviceError(f"failed to start xcodebuild: {exc}") from exc

        driver = make_driver(self._actuator, self._udid, runner_port=self._runner_port)
        # A cold `xcodebuild test-without-building` spins up the XCTest host and launches the app
        # before the runner's server answers /health; on a loaded CI runner that first start well
        # exceeds the 10s default, so give it generous headroom (a warm start still returns at once).
        cast(base.BackendLifecycle, driver).await_ready(timeout=_RUNNER_STARTUP_TIMEOUT)
        return driver

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        if self._runner_proc is not None:
            self._runner_proc.terminate()
            try:
                self._runner_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._runner_proc.kill()
                self._runner_proc.wait()
            self._runner_proc = None
        if self._patched_runner is not None:
            self._patched_runner.unlink(missing_ok=True)
            self._patched_runner = None
        super().teardown(driver, eff)


def _patch_xctestrun_env(runner_path: Path, forwarded: Mapping[str, str]) -> Path:
    """Write a copy of the .xctestrun with *forwarded* merged into each target's env.

    `xcodebuild` does not propagate its own environment into the Simulator test-runner
    process, so the runner reads `BAJUTSU_*` from `TestingEnvironmentVariables` (the runner
    process's env) instead. Returns the temp copy's path; the caller unlinks it on teardown.
    """
    with runner_path.open("rb") as f:
        plist = plistlib.load(f)
    for key, target in plist.items():
        if key == "__xctestrun_metadata__" or not isinstance(target, dict):
            continue
        env_vars = dict(target.get("TestingEnvironmentVariables") or {})
        env_vars.update(forwarded)
        target["TestingEnvironmentVariables"] = env_vars
    # `__TESTROOT__` in the plist resolves relative to the .xctestrun's own directory, so the
    # patched copy must sit beside the original (next to the built products) to still find them.
    fd, path = tempfile.mkstemp(suffix=".xctestrun", dir=str(runner_path.parent))
    with os.fdopen(fd, "wb") as f:
        plistlib.dump(plist, f)
    return Path(path)


def environment_for(
    actuator: str, udid: str, env_run: simctl.RunFn = simctl._real_run
) -> Environment:
    """The `Environment` for *actuator* — the seam that ends per-actuator branching in the runner."""
    if actuator == "playwright":
        return WebEnvironment(actuator)
    if actuator == "adb":
        # The Android environment drives adb (its own `argv -> stdout` runner), not simctl, so the
        # simctl-typed `env_run` does not apply — it uses adb's default runner.
        return AndroidEnvironment(actuator, udid)
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
    id_namespaces: list[str] | None = None,
) -> None:
    """Poll until the launched app has rendered its first screen.

    Readiness is decided by the strongest signal available, in order:

    - `ready_sel` (a target's `readyWhen`): wait for that element to appear — the signal for an app
      whose first interactive screen is a modal over always-present chrome, where a count heuristic
      would return before the modal presents.
    - `id_namespaces` (a target's `idNamespaces`): wait for any element whose id belongs to a declared
      namespace. On a slow cold boot the device query can return SpringBoard (the Home screen's app
      icons) before the app foregrounds — 2+ *off-namespace* elements that a bare count would wrongly
      accept, letting the first scenario step race the real launch and time out. Requiring an
      in-namespace element proves the app itself is on screen.
    - neither: fall back to "more than the app root element" (any 2+ elements).

    Uses exponential backoff: the first poll is short (the app is often ready quickly) and subsequent
    intervals double up to `poll_max`, reducing wasted subprocess calls when the app takes longer to
    start.
    """
    deadline = time.monotonic() + timeout
    poll = min(poll_init, poll_max)
    # Use the selector only when it has a per-element condition; otherwise (None, empty, or
    # positional-only like `index`) fall back to the namespace/count heuristics — an all-matching
    # selector would return on a single element, weaker than "in-namespace" or "2+".
    match_sel = ready_sel if ready_sel and any(k in ready_sel for k in _READY_MATCH_KEYS) else None
    declared = set(id_namespaces or ())
    while time.monotonic() < deadline:
        try:
            elements = driver.query()
            if match_sel is not None:
                ready = len(base.find_all(elements, match_sel)) >= 1
            elif declared:
                ready = any(
                    el["identifier"] is not None and namespace_of(el["identifier"]) in declared
                    for el in elements
                )
            else:
                ready = len(elements) >= 2
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


def _web_relauncher(
    driver: base.Driver,
    ready_sel: base.Selector | None = None,
    id_namespaces: list[str] | None = None,
) -> RelaunchFn:
    """Web `relaunch`: re-navigate to the base URL and wait until ready (no device restart)."""

    def relaunch(opts: Relaunch) -> None:
        cast(base.BackendLifecycle, driver).navigate()  # web-only lifecycle
        _await_ready(driver, ready_sel=ready_sel, id_namespaces=id_namespaces)

    return relaunch


def device_relauncher(
    udid: str, env_run: simctl.RunFn = simctl._real_run, extra_env: Mapping[str, str] | None = None
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
    e = simctl.Env(udid, run=env_run)

    def for_scenario(eff: Effective, scenario: Scenario, driver: base.Driver) -> RelaunchFn:
        pre = scenario.preconditions
        bundle_id = require_ios(eff).bundle_id

        def relaunch(opts: Relaunch) -> None:
            e.terminate(bundle_id)
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
                *simctl.locale_args(locale),
            ]
            e.launch(bundle_id, launch_args, launch_env)
            _await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

        return relaunch

    return for_scenario


def device_control(
    udid: str, bundle_id: str, env_run: simctl.RunFn = simctl._real_run
) -> DeviceControl:
    """A `DeviceControl` bound to one device.

    Backs the `setLocation` / `push` / `clearKeychain` / `clearClipboard` / `setClipboard` /
    `background` / `foreground` / `overrideStatusBar` / `clearStatusBar` steps and the `clipboard`
    assertion (read-back) via simctl.

    Args:
        udid: The target device.
        bundle_id: The app the control acts on (e.g. for `push` / `foreground`).
        env_run: The subprocess runner for simctl, injectable for tests.
    """
    e = simctl.Env(udid, run=env_run)

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


def android_device_control(
    serial: str, package: str, env_run: adb.RunFn = adb._real_run
) -> DeviceControl:
    """A `DeviceControl` for the Android emulator, backing only the operations it can honor.

    `setLocation` (`emu geo fix`) runs over the emulator console; the clipboard operations run over
    an ordered `am broadcast` to the app's in-app receiver (BajutsuAndroid, BE-0233) — hence `package`,
    to address the broadcast at the app under test. `push` / `clearKeychain` / the status-bar overrides
    / the app-lifecycle steps have no faithful emulator equivalent and raise `UnsupportedAction`.
    Preflight (BE-0212) rejects those steps up front from the adb backend's advertised subset, so this
    raise is the runtime backstop, never a silent no-op.

    Args:
        serial: The target emulator/device serial.
        package: The app under test's package, addressed by the clipboard broadcast.
        env_run: The subprocess runner for adb, injectable for tests.
    """
    e = adb.Env(serial, run=env_run)

    def _unsupported(op: str) -> base.UnsupportedAction:
        return base.UnsupportedAction(f"{op} is not supported on the Android emulator")

    class _Control:
        def set_location(self, lat: float, lon: float) -> None:
            e.set_location(lat, lon)

        def set_clipboard(self, text: str) -> None:
            e.set_clipboard(package, text)

        def get_clipboard(self) -> str:
            return e.get_clipboard(package)

        def clear_clipboard(self) -> None:
            e.clear_clipboard(package)

        def push(self, payload: dict[str, object]) -> None:
            raise _unsupported("push")

        def clear_keychain(self) -> None:
            raise _unsupported("clearKeychain")

        def home(self) -> None:
            raise _unsupported("background")

        def foreground(self) -> None:
            raise _unsupported("foreground")

        def override_status_bar(self, **kwargs: str | int) -> None:
            raise _unsupported("overrideStatusBar")

        def clear_status_bar(self) -> None:
            raise _unsupported("clearStatusBar")

    return _Control()
