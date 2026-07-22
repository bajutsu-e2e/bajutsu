"""The device-style lifecycle base shared by the simctl family.

`_DeviceEnvironment` holds every lease-shaping method the simctl family (XCUITest, fake) share; each
concrete environment adds only its own `start` sequence.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path

from bajutsu import simctl
from bajutsu.config import Effective, require_ios
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.drivers import base
from bajutsu.evidence import intervals
from bajutsu.evidence.network import Collector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.platform_lifecycle import readiness
from bajutsu.platform_lifecycle.device_control import device_control
from bajutsu.platform_lifecycle.relaunchers import device_relauncher
from bajutsu.scenario import Scenario


class _DeviceEnvironment:
    """The device-style lifecycle: the iOS Simulator (`simctl`) backend and the fake test backend,
    which mimics the same shape without a real device.

    Only `start` differs between them — the fake runs no device sequence — so every lease-shaping
    method (catalog, relaunch, control, teardown, the external-receiver network strategy) lives here.
    """

    def __init__(
        self,
        actuator: str,
        udid: str,
        env_run: simctl.RunFn = simctl._real_run,
        *,
        spawn: intervals.Spawn = intervals._spawn,
    ) -> None:
        self._actuator = actuator
        self._udid = udid
        self._run = env_run
        # How pre-launch interval processes are spawned (simctl recordVideo); injectable for tests.
        self._spawn = spawn
        # A video recording begun before the app launched, for the sink to adopt (BE video timing).
        self._prestarted_video: intervals.Interval | None = None

    def resolve_device(self, udid: str) -> str:
        return simctl.resolve_udid(udid, self._run)

    def prestarted_intervals(self) -> list[intervals.Interval]:
        """Interval captures begun during `start()`, before the app launched, for the sink to adopt.

        Empty on a backend that records on demand; the simctl family fills it with the scenario video
        started before launch so the app's cold start is recorded rather than missed.
        """
        return [self._prestarted_video] if self._prestarted_video is not None else []

    def _prestart_video(self, record_video_dir: Path | None) -> None:
        """Begin the scenario video (simctl recordVideo) before the app launches; None records nothing.

        The recording writes to a temp file under `record_video_dir`; the sink adopts it at scenario
        start and relocates it to the artifact path on stop (`intervals.adopt`). Filed under the udid
        so concurrent device lanes writing into the shared dir never collide.
        """
        if record_video_dir is None:
            return
        self._prestarted_video = intervals.start_video(
            self._udid, record_video_dir / f"prestart-{self._udid}.mp4", spawn=self._spawn
        )

    def _stop_prestarted_video(self) -> None:
        """Finalize and discard a pre-started recording after a *failed* launch.

        Only the failure path calls this: on success the sink adopts the running interval. A launch
        that fails after `_prestart_video` would otherwise leave `recordVideo` running — an orphaned
        session wedges every later capture on the same simulator (`start_video`'s own warning) — so
        stop it and drop the orphan temp file. Best-effort: a cleanup error must not mask the launch
        error being re-raised.
        """
        interval = self._prestarted_video
        if interval is None:
            return
        self._prestarted_video = None
        with contextlib.suppress(Exception):
            interval.stop().unlink(missing_ok=True)

    def captures_video(self) -> bool:
        return True  # a simctl-backed device records a scenario-wide video via a simctl interval

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return simctl.device_catalog(self._run)

    def observes_network_via_driver(self) -> bool:
        return False  # the app reports to an external collector via BAJUTSU_COLLECTOR

    def records_video_up_front(self) -> bool:
        return False  # simctl records on demand

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        raise NotImplementedError("device backends observe network via an external receiver")

    def bridge_collector(self, port: int) -> Callable[[], None]:
        return lambda: None  # the Simulator shares the Mac's loopback; nothing to bridge

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

    def has_reusable_resident(self) -> bool:
        # fake spawns no resident to amortize; XcuitestEnvironment overrides (BE-0291).
        return False

    def end_lease(self, driver: base.Driver, eff: Effective) -> None:
        # No warm resident here, so a lease's end is just its full teardown (BE-0291).
        self.teardown(driver, eff)

    def has_devices(self) -> bool:
        return True

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        udids = [self.resolve_device(u.strip()) for u in udid_arg.split(",") if u.strip()]
        return udids[: max(1, min(workers, len(udids)))]

    def crawl_reset(self, eff: Effective) -> Reset:
        # Return to a clean start the way `run` reaches any state: relaunch (not a full erase) so each
        # frontier revisit stays fast; the engine then replays the shortest path from the entry.
        e = simctl.Env(self._udid, run=self._run)
        bundle_id = require_ios(eff).bundle_id

        def reset(driver: base.Driver) -> None:
            e.terminate(bundle_id)
            e.launch(bundle_id, [*eff.launch_args, *simctl.locale_args(eff.locale)], eff.launch_env)
            readiness._await_ready(
                driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces
            )

        return reset

    def crawl_aliveness(self) -> AliveCheck | None:
        return None  # the engine reads the accessibility tree for device crash detection

    def crawl_recover(self) -> Recover | None:
        return None  # no in-lane recovery: a wedged device surfaces as a DeviceError

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        return None  # OS prompts are handled by the optional alert guard, wired by the CLI
