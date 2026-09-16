"""The Android emulator lifecycle over adb — the adb backend's environment."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.common import backends
from bajutsu.common.backend_cli import adb
from bajutsu.common.config import Effective, require_android
from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals
from bajutsu.common.evidence.network import Collector
from bajutsu.common.orchestrator import DeviceControl, RelaunchFn
from bajutsu.common.platform_lifecycle import readiness
from bajutsu.common.platform_lifecycle.device_control import android_device_control
from bajutsu.common.platform_lifecycle.protocols import ProvisionProfile
from bajutsu.common.scenario import Preconditions, Relaunch, Scenario
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset

from .resident_server_like import ResidentServerLike

if TYPE_CHECKING:
    from bajutsu.common.backend_cli.adb_resident import ResidentChannel

logger = logging.getLogger(__name__)

# Overrides the resident UI Automator read channel (BE-0245). By default the channel is on whenever
# the server APKs are built (`make -C BajutsuAndroidUIAutomatorServer build`) and off otherwise, so a fresh clone
# reads via `uiautomator dump` exactly as before. Set to 0/false/no to force the dump path even on a
# built tree; set to 1/true/yes to force the resident path (start() degrades loudly to dump if it is
# not built). Either way a channel failure falls back to `uiautomator dump`.
_RESIDENT_ENV = "BAJUTSU_ADB_RESIDENT"

# The three renderings one `date` read yields for a launch marker (BE-0424): the epoch, exit-info's
# `timestamp=` form, and `logcat -t`'s. A read returning anything else is treated as no marker.
_LAUNCH_MARKER_FIELDS = 3

# How long the `logcat` crash-block extraction re-dumps for, and how often. Bounded the same way, and
# for the same reason, as the driver's exit-info poll: `crash_dump` writes a native block after the
# death the detection already keyed off, so one snapshot can legitimately come up empty (BE-0424).
_LOGCAT_TIMEOUT = 5.0
_LOGCAT_POLL = 0.25


def _reset_exit_info_poll(driver: base.Driver) -> None:
    """Let a genuinely fresh launch restore the driver's app-crash poll bound (BE-0424).

    `isinstance` against the narrow `AppCrashPollResettable` protocol, the same shape
    `SettledCacheInvalidator` is checked with two lines above each call site: a concrete-class check
    would silently stop working the moment the driver reaches here through a wrapper (`TracingDriver`
    installs protocol members as real attributes precisely so this kind of check keeps reading true).
    """
    if isinstance(driver, base.AppCrashPollResettable):
        driver.reset_exit_info_poll()


def _newest_tombstone(listing: str, launched_at: float) -> str | None:
    """The newest `/data/tombstones` entry modified at or after *launched_at*, or None (BE-0424).

    Both sides of the comparison are the *device's* own seconds-since-epoch — `stat -c "%Y"` here,
    `date +%s` when the marker was stamped — so this is a plain number comparison and never resolves
    a device-side rendering through the host's timezone, the rule the whole marker design turns on.

    On API 30+ (this item's own floor) `debuggerd` writes both `tombstone_NN` (text) and its
    protobuf sibling `tombstone_NN.pb`, and the `tombstone_*` glob this reads matches both — so the
    `.pb` file is skipped here rather than in the shell glob: a `cat` of binary protobuf through the
    redacting text path would either mangle it (`errors="replace"`) or raise, silently dropping the
    whole layer.
    """
    newest: tuple[float, str] | None = None
    for line in listing.splitlines():
        epoch, _, path = line.strip().partition(" ")
        if path.endswith(".pb"):
            continue
        try:
            mtime = float(epoch)
        except ValueError:
            continue
        if mtime >= launched_at and (newest is None or mtime > newest[0]):
            newest = (mtime, path.rsplit("/", 1)[-1])
    return newest[1] if newest is not None else None


class AndroidEnvironment:
    """The Android emulator lifecycle via `adb` (the adb backend's environment).

    `start` runs the adb sequence — boot-readiness wait → optional APK (re)install → `pm clear` for a
    clean state (the `erase` equivalent) → `am force-stop` → runtime-permission pre-grant (`pm grant`,
    BE-0210) → `am start` (launch env forwarded as intent extras) → deeplink — and returns the `adb`
    driver. The lease-shaping methods mirror the iOS
    `_DeviceEnvironment`, over `adb` instead of `simctl`: the same seam, a different subprocess tool.
    Network is not observed *natively* (the adb driver declares no `NETWORK` capability), but the app
    reports its own exchanges to the host collector, which `bridge_collector` reaches over `adb reverse`
    (BE-0283) — the same app-side capture iOS relies on, so a `request` assertion is satisfied without a
    native monitor. Device control backs the subset the emulator can honor
    (`setLocation`, BE-0211, plus clipboard through the app's in-app receiver, BE-0233); the rest of
    the family stays unsupported.
    """

    def __init__(
        self,
        actuator: str,
        serial: str,
        adb_run: adb.RunFn = adb.real_run,
        *,
        resident_factory: Callable[[], ResidentServerLike] | None = None,
        provision: ProvisionProfile | None = None,
        spawn: intervals.Spawn = intervals.spawn,
    ) -> None:
        self._actuator = actuator
        self._serial = serial
        self._run = adb_run
        # How pre-launch interval processes are spawned (adb screenrecord); injectable for tests.
        self._spawn = spawn
        # A video recording begun before the app launched, for the sink to adopt (video timing).
        self._prestarted_video: intervals.Interval | None = None
        # The app under test's package, stashed at `start()` so `app_crash_artifacts()` — which takes
        # no arguments — can bound its `logcat` extraction to this process (BE-0424).
        self._package: str | None = None
        # The device-clock instant of the most recent launch, in the two renderings its two consumers
        # need: the epoch (the tombstone mtime bound) and exit-info's own `timestamp=` form, handed to
        # the driver as a live read. Stamped before each of the three launch sites, never after.
        self._launch_marker: tuple[float, str] | None = None
        # The same instant in `logcat -t`'s own format, which is a third rendering again — `logcat`
        # reads a bare integer as a line count, and its format carries no year where exit-info's does,
        # so neither of the two above substitutes for it.
        self._logcat_marker: str | None = None
        # Override the resident-server construction in tests; None uses the real, env-gated default.
        self._resident_factory = resident_factory
        self._resident: ResidentServerLike | None = None
        # Which resident-server APK pair this run has already installed, per serial (BE-0407 unit
        # 22). Lives here, not on the per-lease `ResidentServer`, because that is the whole point:
        # every lease after the first was putting back the same bytes it had just uninstalled.
        self._installed_resident_apks: dict[str, tuple[str, str]] = {}
        # A device provider's readiness report (BE-0236); the inert default is a locally-attached
        # device, so `start` runs the full boot wait / install unless a cloud provider says otherwise.
        self._provision = provision or ProvisionProfile()

    def resolve_device(self, udid: str) -> str:
        return adb.resolve_serial(udid, self._run)

    def captures_video(self) -> bool:
        return False  # screenrecord is a driver-interval capture, not the record command's video

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        android = require_android(eff)
        # `app_crash_artifacts()` takes no arguments, so this is the only route the `logcat` extraction
        # has to the package it must bound itself to (BE-0424).
        self._package = android.package
        e = adb.Env(self._serial, run=self._run)
        try:
            # A device provider that hands over an already-booted device / an already-installed build
            # lets us skip the boot wait and the install (BE-0236); the local provider leaves both
            # flags off, so a locally-attached device runs the full sequence exactly as before. Both
            # skips still fail loudly if the provider's claim is wrong: a not-actually-booted device
            # trips the very next `pm clear` / `am start`, and a genuinely-absent app has no launcher
            # activity, so `am start` → `resolve_activity` raises a clean DeviceError — a false profile
            # never degrades into a silent pass.
            if not self._provision.boot_ready:
                readiness.await_boot(e)
            if android.app_path and not self._provision.app_preinstalled:
                if not Path(android.app_path).exists():
                    raise adb.DeviceError(
                        f"appPath not found: {android.app_path} (build the app first)"
                    )
                # Only where the data is going anyway. A clean reinstall may cross a signing key or
                # drop a component the old build had, and `install -r` cannot express either — but on
                # the keep-data path removing the package first would throw away exactly what that
                # path exists to preserve, so there `install -r` is left to fail loudly instead.
                if pre.erase or pre.reinstall == "clean":
                    e.uninstall(android.package)
                e.install(android.app_path)
            # `pm clear` is the clean-state reset (fresh app data); skip it only on an explicit
            # `overwrite` reinstall with no erase, matching iOS's "keep data" overwrite path.
            if pre.erase or pre.reinstall == "clean":
                e.clear(android.package)
            e.force_stop(android.package)  # clean start so readiness reflects the new launch
            # Grant runtime permissions after `pm clear` (which resets grants) but before launch, so
            # a permission prompt never blocks the scenario — deterministic, no timing (BE-0210).
            e.grant_permissions(android.package, android.grant_permissions)
            # The per-scenario field (BE-0276), applied the same way: after clear, before launch.
            # Layers on top of the config-level grant above — a scenario can revoke a config-granted
            # permission to exercise the denied-path flow.
            if permissions:
                e.apply_permissions(android.package, permissions)
            launch_env: Mapping[str, str] = {
                **eff.launch_env,
                **pre.launch_env,
                **(extra_env or {}),
            }
            # Start the scenario video now — the device is up and the app installed, but not yet
            # launched — so the recording spans the app's cold start rather than missing it.
            self._prestart_video(record_video_dir)
            try:
                # Stamped *before* `e.launch`, never after (BE-0424): `e.launch` is `am start -W`,
                # whose `-W` waits for the launch to complete, so a marker taken once it returns has
                # already been passed by a startup crash — and would then reject that crash's own
                # `logcat` block and exit-info entry, both timestamped before it.
                self._stamp_launch_marker()
                e.launch(android.package, launch_env)
                if pre.deeplink is not None:
                    e.open_url(pre.deeplink, android.package)
            except BaseException:
                self._stop_prestarted_video()  # a failed launch must not leak the screenrecord
                raise
        except subprocess.CalledProcessError as exc:
            raise adb.device_error(exc) from exc
        except OSError as exc:
            # adb itself could not be run (e.g. missing from PATH) — surface it as a clean
            # DeviceError (exit 2) rather than an unhandled traceback or a spin to the boot deadline.
            raise adb.DeviceError(
                f"could not run adb ({exc}); is Android platform-tools installed and on PATH?"
            ) from exc
        # The resident read channel drives whatever app is now on screen (BE-0245); a startup failure
        # degrades to `uiautomator dump` rather than failing the lease.
        channel = self._begin_resident(native_z=android.native_z)
        fetch = channel.fetch if channel is not None else None
        clock = channel.clock if channel is not None else None
        act = channel.act if channel is not None else None
        return backends.make_driver(
            self._actuator,
            self._serial,
            fetch_hierarchy=fetch,
            fetch_clock=clock,
            act=act,
            package=android.package,
            api_level=self._read_api_level(),
            # A live read, not a value frozen at construction — the same seam `fetch_clock` uses —
            # so a mid-scenario `relaunch` moves the bound the driver compares against (BE-0424).
            launched_at=lambda: self._launch_marker,
        )

    def _read_api_level(self) -> int | None:
        """The device's SDK level, or None when it cannot be read (BE-0424).

        Nothing else in this codebase tracks it. The driver needs it because `ApplicationExitInfo`
        does not exist below API 30, and an unreadable level fails the probe closed rather than
        letting it poll for a signal the device may never report.
        """
        try:
            return int(self._run(adb.get_prop_cmd(self._serial, "ro.build.version.sdk")).strip())
        except (subprocess.CalledProcessError, OSError, ValueError):
            return None

    def _stamp_launch_marker(self) -> None:
        """Record the device-clock instant of the launch about to happen, in all three renderings.

        One `date` read, split on the host into the epoch (the tombstone mtime bound), the exit-info
        `timestamp=` rendering, and `logcat -t`'s — see `adb.launch_marker_cmd` for why one read
        rather than three, and why no rendering is derived from another on the host. A read that
        fails leaves the marker unset, which makes every consumer answer "cannot confirm" (BE-0424).
        """
        try:
            fields = self._run(adb.launch_marker_cmd(self._serial)).strip().split("|")
        except (subprocess.CalledProcessError, OSError):
            fields = []
        if len(fields) != _LAUNCH_MARKER_FIELDS:
            self._launch_marker, self._logcat_marker = None, None
            return
        epoch, exit_info_stamp, logcat_stamp = fields
        try:
            self._launch_marker = (float(epoch), exit_info_stamp)
        except ValueError:
            self._launch_marker, self._logcat_marker = None, None
            return
        self._logcat_marker = logcat_stamp

    def _begin_resident(self, *, native_z: bool = False) -> ResidentChannel | None:
        """Start the resident server for this lease, or None to read via `uiautomator dump`."""
        server = self._make_resident(native_z=native_z)
        if server is None:
            return None
        from bajutsu.common.drivers.adb import AdbResidentError

        try:
            channel = server.start()
        except AdbResidentError as exc:
            logger.warning(
                "resident UI Automator server unavailable (%s); reading via `uiautomator dump`, "
                "actuating via coordinates",
                exc,
            )
            return None
        self._resident = server
        return channel

    def _make_resident(self, *, native_z: bool = False) -> ResidentServerLike | None:
        if self._resident_factory is not None:
            return self._resident_factory()
        from bajutsu.common.backend_cli.adb_resident import ResidentServer, server_apks_built

        override = os.environ.get(_RESIDENT_ENV, "").strip().lower()
        if override in {"0", "false", "no"}:
            # Explicit opt-out: reads go through `uiautomator dump` and gestures inject a coordinate —
            # the declared degraded mode, not a silent one (BE-0339 Unit 4), logged at the same level
            # as the "selected" branch below rather than louder, since opting out is an ordinary
            # configuration choice, not a fault.
            logger.debug(
                "%s=%s: reading via `uiautomator dump`, actuating via coordinates",
                _RESIDENT_ENV,
                override,
            )
            return None
        # Default-on by APK presence: route reads through the resident server whenever it is built.
        # A truthy override forces it on even before a build (start() then degrades loudly to dump).
        forced_on = override in {"1", "true", "yes"}
        if not forced_on and not server_apks_built():
            logger.debug(
                "resident UI Automator server APKs not built; reading via `uiautomator dump`, "
                "actuating via coordinates (BE-0339 Unit 4)"
            )
            return None
        # The choice now flips on whatever is built on disk, not an explicit flag, so log which
        # channel it landed on (and why) — otherwise a stale local build silently switching a run
        # onto the resident channel is invisible without inspecting build-output paths.
        logger.debug(
            "resident UI Automator channel selected (%s)",
            f"{_RESIDENT_ENV} override" if forced_on else "server APKs built",
        )
        # `_installed_resident_apks` outlives the lease this server is built for, so a run's later
        # leases can skip putting the very same APK pair back on the device (BE-0407 unit 22).
        return ResidentServer(
            self._serial,
            run=self._run,
            installed=self._installed_resident_apks,
            native_z=native_z,
        )

    def device_catalog(self) -> dict[str, dict[str, str]]:
        return adb.device_catalog(self._run)

    def observes_network_via_driver(self) -> bool:
        # No native network monitor to actuate, so the app reports each exchange to the host
        # collector over `bridge_collector`'s `adb reverse` tunnel instead (BE-0283) — the same
        # external-receiver shape as iOS, and a real capture rather than a mocked one.
        return False

    def mirrors_collector_port_on_device(self) -> bool:
        # `bridge_collector`'s `adb reverse tcp:<port> tcp:<port>` binds the same number inside the
        # guest, so the collector's port must be one the emulator can bind too.
        return True

    def records_video_up_front(self) -> bool:
        # Begin `screenrecord` before the app launches so its cold start is captured; the sink adopts
        # the running interval (`_prestart_video` / `prestarted_intervals`) instead of the driver's
        # on-demand `driver_interval("video")`. The pool reads this to wire `record_video_dir` in.
        return True

    def prestarted_intervals(self) -> list[intervals.Interval]:
        """Interval captures begun during `start()`, before the app launched, for the sink to adopt.

        Holds the scenario video started before launch (the adb twin of the iOS path), so the app's
        cold start is recorded; empty when no video was requested.
        """
        return [self._prestarted_video] if self._prestarted_video is not None else []

    def _prestart_video(self, record_video_dir: Path | None) -> None:
        """Begin the scenario video (`adb screenrecord`) before the app launches; None records nothing.

        The device-side recording is adopted by the sink at scenario start and pulled to the artifact
        path on stop (`intervals.adopt` wrapping `start_screenrecord`'s pull). Filed under the serial
        so concurrent device lanes writing into the shared dir never collide. `confirm_started`
        makes this block until the device-side process is confirmed (or the confirmation times out)
        — this call sits immediately before `start()`'s `e.launch(...)`, with nothing running
        concurrently, so that wait lands on the scenario's critical path.
        """
        if record_video_dir is None:
            return
        self._prestarted_video = intervals.start_screenrecord(
            self._serial,
            record_video_dir / f"prestart-{self._serial}.mp4",
            spawn=self._spawn,
            run=self._run,
            confirm_started=True,
        )

    def _stop_prestarted_video(self) -> None:
        """Finalize and discard a pre-started recording after a *failed* launch.

        Only the failure path calls this: on success the sink adopts the running interval. A launch
        that fails after `_prestart_video` would otherwise leave `screenrecord` running — leaking the
        local `adb shell` client and the device-side recording (up to its ~180s cap) plus the temp
        file — so stop it (finalizing the device side) and drop the orphan file. Best-effort: a
        cleanup error must not mask the launch error being re-raised.
        """
        interval = self._prestarted_video
        if interval is None:
            return
        self._prestarted_video = None
        with contextlib.suppress(Exception):
            interval.stop().unlink(missing_ok=True)

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        raise NotImplementedError("the adb backend does not observe network via the driver")

    def bridge_collector(self, port: int) -> Callable[[], None]:
        # The emulator's 127.0.0.1 is its own loopback, not the host's, so tunnel the collector port
        # back to the host with `adb reverse` — the injected BAJUTSU_COLLECTOR URL then resolves
        # on-device unchanged (BE-0283). The reverse-direction twin of the resident server's
        # forward_cmd (host → device); here the device reaches out to the host.
        try:
            self._run(adb.reverse_cmd(self._serial, port))
        except subprocess.CalledProcessError as exc:
            raise adb.device_error(exc) from exc
        except OSError as exc:
            # adb itself could not be run — surface a clean DeviceError, as start() does above,
            # rather than let a raw OSError escape lease().
            raise adb.DeviceError(
                f"could not run adb ({exc}); is Android platform-tools installed and on PATH?"
            ) from exc

        def remove() -> None:
            # Best-effort teardown: a failed remove (the device already gone) must not mask the
            # lease's own outcome, so it's swallowed rather than raised — but logged (mirroring
            # _begin_resident's degrade-with-a-log-line below), so a genuinely stuck tunnel is still
            # visible to someone debugging a flaky Android lane rather than silently invisible.
            try:
                self._run(adb.reverse_remove_cmd(self._serial, port))
            except (subprocess.CalledProcessError, OSError) as exc:
                logger.warning("adb reverse --remove failed for port %d: %s", port, exc)

        return remove

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
            self._stamp_launch_marker()  # before the launch, per `start` (BE-0424)
            e.launch(package, launch_env)
            # `force_stop`/`launch` replace the screen through `adb.Env`, never through the driver's
            # own actuators — the one door `AdbDriver._settled_key` needs closed that its actuators
            # cannot close themselves (`base.SettledCacheInvalidator`, BE-0351).
            if isinstance(driver, base.SettledCacheInvalidator):
                driver.invalidate_settled_cache()
            # A genuinely fresh launch, so the app-crash probe's exit-info bound is worth paying
            # again — a separate seam from the cache invalidation above, which every ordinary gesture
            # also triggers and which would therefore clear this latch far too eagerly (BE-0424).
            _reset_exit_info_poll(driver)
            readiness.await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

        return relaunch

    def controller(self, eff: Effective) -> DeviceControl | None:
        # The emulator-backed subset (setLocation over the console + clipboard over the app's in-app
        # receiver, BE-0233); the rest of the family raises UnsupportedAction, and preflight (BE-0212)
        # rejects it up front from the adb capability set. Clipboard addresses its broadcast at the
        # app under test, so the package is threaded through.
        return android_device_control(self._serial, require_android(eff).package, self._run)

    def teardown(self, driver: base.Driver, eff: Effective) -> None:  # noqa: ARG002  # Environment shape
        # Stop the resident server first (BE-0245) so no instrumentation is left running on the device,
        # then force-stop the app — this runs in the run's finally, so it fires on failure/interrupt too.
        if self._resident is not None:
            self._resident.stop()
            self._resident = None
        adb.Env(self._serial, run=self._run).force_stop(require_android(eff).package)

    def has_reusable_resident(self) -> bool:
        # The UI Automator read channel (BE-0245) is torn down per lease; amortizing it across leases
        # is out of scope for BE-0291 (which targets the XCUITest runner's cold startup).
        return False

    def request_device_replacement(self) -> None:
        # Nothing to serve the request with: an emulator or handset is brought up out of band, and
        # the wedge shape BE-0354 escalates for is the XCUITest runner channel's, which this backend
        # has no counterpart to. Restarting the emulator process stays the separate follow-up BE-0353
        # named. The crash retry keeps its forced-erase rung here, unchanged.
        return None

    def replaced_device(self) -> str | None:
        # An adb device (emulator or handset) is brought up out of band, so this lifecycle never
        # creates one to replace it.
        return None

    def take_crash_snapshot(self) -> Callable[[], list[tuple[str, bytes]]]:
        # This backend spawns no host-side resident whose output it captures: the UI Automator server
        # runs on the device and its failures reach the driver directly, so there is no host log or
        # crash report of its own to copy into the scenario's directory (BE-0421).
        return list

    def app_crash_artifacts(self) -> list[tuple[str, bytes]]:
        """The `logcat` crash block for the app under test's own crash (BE-0424).

        The always-available layer: it needs no elevated access and touches no channel a later step
        in this scenario still needs, which is what makes it safe to call the moment the crash is
        confirmed. The tombstone layer is `app_crash_tombstone()` instead, for exactly that reason.

        Nothing is cleared. The crash buffer is device-global and persists across launches, so
        `scripts/collect_android_diagnostics.sh`'s end-of-job sweep still needs everything before this
        launch; the `-t` bound is what keeps a stale crash out without destroying it.
        """
        try:
            return self._logcat_crash()
        except Exception as exc:
            logger.debug("android: the app-crash logcat extraction failed (%s)", exc, exc_info=True)
            return []

    def _logcat_crash(self) -> list[tuple[str, bytes]]:
        """Poll the crash buffer for a block belonging to this app since this launch (BE-0424)."""
        if self._package is None or self._logcat_marker is None:
            return []
        for _ in base.deadline_ticks(_LOGCAT_TIMEOUT, _LOGCAT_POLL):
            # Re-dumped rather than trusted on one `-d` snapshot: `crash_dump` writes a native
            # `>>> <process> <<<` block only *after* the death `pidof` already reported, the same
            # asynchrony the driver's own exit-info poll exists to close.
            try:
                text = self._run(adb.logcat_crash_dump_cmd(self._serial, self._logcat_marker))
            except (subprocess.CalledProcessError, OSError):
                return []
            block = adb.extract_crash_block(text, self._package)
            if block is not None:
                return [("logcat-crash.txt", block.encode())]
        return []

    def app_crash_tombstone(self) -> list[tuple[str, bytes]]:
        """The native tombstone for the same crash, best-effort and root-gated (BE-0424).

        Called only from `pipeline.py`'s post-return scan. `adb root` restarts `adbd`, which kills the
        resident server's `am instrument -w` session and drops BE-0283's `adb reverse` tunnel — so
        firing it mid-scenario would make every outcome still to come raise `BackendCrashError`
        against a dead channel and discard the whole `RunResult`. Neither is re-established here:
        nothing later in this lease needs them, and `start()` rebuilds both from scratch on the next
        one regardless.

        A real device, a user build, or a refused `adb root` all resolve to skipping this layer
        silently. The event is still reported and `logcat-crash.txt` still lands; what is lost is the
        native-frame detail a managed-code crash never needed in the first place.
        """
        if self._launch_marker is None:
            return []
        try:
            return self._pull_tombstone(self._launch_marker[0])
        except Exception as exc:
            logger.debug("android: the tombstone pull failed (%s)", exc, exc_info=True)
            return []
        finally:
            self._restore_unroot()

    def _pull_tombstone(self, launched_at: float) -> list[tuple[str, bytes]]:
        """`adb root`, then the newest tombstone written at or after *launched_at* (BE-0424)."""
        self._run(adb.root_cmd(self._serial))
        # Without this every command below races `adbd`'s restart — the same gate
        # `collect_android_diagnostics.sh` puts between its own `adb root` and its `adb pull`.
        self._run(adb.wait_for_device_cmd(self._serial))
        listing = self._run(adb.tombstones_cmd(self._serial))
        name = _newest_tombstone(listing, launched_at)
        if name is None:
            return []
        return [(name, self._run(adb.cat_cmd(self._serial, f"/data/tombstones/{name}")).encode())]

    def _restore_unroot(self) -> None:
        """Hand the device back at the privilege level every other lease already assumes (BE-0424).

        `adb root` persists device-wide until `adb unroot` or a reboot, and nothing else in this
        repository restores it. Left leaked, a later scenario on this device would run its
        `install` / `pm clear` / `force_stop` / `launch` through a root shell — and, sharper,
        `AdbDriver._rooted()` caches `id -u` and two actuation decisions read it, so a two-finger
        gesture that should fail loudly with `UnsupportedAction` would instead run and pass, decided
        by whether an earlier, unrelated scenario happened to crash.

        Verified, not merely attempted: swallowing the failure the way the pull's own errors are
        swallowed is exactly what would let that happen invisibly, so a shell still answering `0`
        afterward is logged loudly.
        """
        try:
            self._run(adb.unroot_cmd(self._serial))
            self._run(adb.wait_for_device_cmd(self._serial))
            still_root = self._run(adb.id_u_cmd(self._serial)).strip() == "0"
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.warning(
                "android: could not restore adbd to unrooted on %s (%s); later scenarios on this "
                "device may actuate differently than they would have",
                self._serial,
                exc,
            )
            return
        if still_root:
            logger.warning(
                "android: adbd on %s is still running as root after `adb unroot`; later scenarios "
                "on this device may actuate differently than they would have",
                self._serial,
            )

    def end_lease(self, driver: base.Driver, eff: Effective) -> None:
        self.teardown(driver, eff)  # no warm resident kept: a lease's end is its full teardown

    def has_devices(self) -> bool:
        return True

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        serials = [self.resolve_device(s.strip()) for s in udid_arg.split(",") if s.strip()]
        return serials[: max(1, min(workers, len(serials)))]

    def crawl_reset(self, eff: Effective) -> Reset:
        package = require_android(eff).package
        e = adb.Env(self._serial, run=self._run)

        def reset(driver: base.Driver) -> None:
            e.force_stop(package)
            self._stamp_launch_marker()  # before the launch, per `start` (BE-0424)
            e.launch(package, eff.launch_env)
            # Same gap as `relauncher` above: this replaces the screen outside the driver's actuators.
            if isinstance(driver, base.SettledCacheInvalidator):
                driver.invalidate_settled_cache()
            # And the same second seam: a crawl builds one driver for the whole walk, so without this
            # the first unconfirmed detection would latch the poll off for every later one (BE-0424).
            _reset_exit_info_poll(driver)
            readiness.await_ready(driver, ready_sel=eff.ready_when, id_namespaces=eff.id_namespaces)

        return reset

    def crawl_aliveness(self) -> AliveCheck | None:
        return None  # the engine reads the accessibility tree for device crash detection

    def crawl_recover(self) -> Recover | None:
        return None  # no in-lane recovery: a wedged device surfaces as a DeviceError

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        return None  # OS prompts are handled by the optional alert guard, wired by the CLI
