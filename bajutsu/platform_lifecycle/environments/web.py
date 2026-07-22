"""The web (Playwright) lifecycle: a fresh browser context is the clean state, `navigate()` the launch."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from bajutsu import backends, simctl
from bajutsu.config import Effective, require_web
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.drivers import base
from bajutsu.evidence import intervals
from bajutsu.evidence.network import Collector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.platform_lifecycle import readiness
from bajutsu.platform_lifecycle.relaunchers import _web_relauncher
from bajutsu.scenario import Preconditions, Scenario


class WebEnvironment:
    """The web (Playwright) lifecycle: a fresh browser context is the clean state and `navigate()`
    is the launch. There is no device to erase/boot/install, so the sequence is just build + navigate;
    network is observed by hooking the live page, video is recorded at context creation, and a release
    closes the browser (no simctl device control).
    """

    def __init__(self, actuator: str) -> None:
        self._actuator = actuator

    def resolve_device(self, udid: str) -> str:
        return udid  # web has no device: the udid passes through untouched

    def captures_video(self) -> bool:
        return False  # Playwright captures video by other means during replay, not while authoring

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        # No OS permission model to pre-set (a browser has no TCC/pm equivalent). Preflight normally
        # rejects a scenario naming one before this is ever reached, but preflight is skippable (a
        # lease driven directly, `capabilities=None` in runner/pipeline.py) — so this is the runtime
        # backstop, the same shape gestures.py's `_require_multi_touch` is for an unsupported gesture.
        if permissions:
            raise base.UnsupportedAction("permissions is not supported on the web backend")
        web = require_web(eff)
        if not web.base_url:
            raise simctl.DeviceError("web backend requires baseUrl (set targets.<name>.baseUrl)")
        driver = backends.make_driver(
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

    def prestarted_intervals(self) -> list[intervals.Interval]:
        # Web's up-front recording is bound to the browser context and adopted through the driver's
        # `driver_interval("video")`, not handed to the sink as a pre-started interval — none here.
        return []

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        from bajutsu.drivers.playwright import PlaywrightDriver

        # A fresh context per lease scopes the traffic; the cast names the web-only collector whose
        # `mocks` param the base Protocol widens to `list[object]`.
        return cast(PlaywrightDriver, driver).network_collector(scenario.mocks)

    def bridge_collector(self, port: int) -> Callable[[], None]:
        return lambda: None  # web observes via the driver; never reached (no external collector)

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

    def has_reusable_resident(self) -> bool:
        return False  # a browser context per lease, no cross-lease resident to amortize (BE-0291)

    def end_lease(self, driver: base.Driver, eff: Effective) -> None:
        self.teardown(driver, eff)  # no warm resident: a lease's end is its full teardown

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
            readiness._await_ready(
                driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces
            )

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
