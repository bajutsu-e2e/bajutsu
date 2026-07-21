"""The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator, or the same
runner without simctl prep on a real device (BE-0019, real-device targeting BE-0238).

This module also isolates the `.xctestrun` packaging helpers (`_patch_xctestrun_env`) and their
`plistlib` / `tempfile` / `shlex` imports, which only XCUITest needs, out of the environment modules
every platform loads.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import socket
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from bajutsu import backends, simctl
from bajutsu.config import Effective, require_ios
from bajutsu.drivers import base
from bajutsu.drivers.xcuitest import XcuitestChannelError
from bajutsu.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.platform_lifecycle.protocols import WarmRunner
from bajutsu.scenario import Preconditions


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

# The bounded /health poll the pool runs before reusing a warm runner (BE-XXXX Unit 4). A responsive
# runner answers at once; a wedged one fails within this window and is discarded as a cache miss, so
# a stuck runner costs one extra cold start rather than hanging the next lease. Reuses the driver's
# existing `await_ready` poll — no second recovery path — with a tight ceiling since the runner is
# already up (unlike the cold `_RUNNER_STARTUP_TIMEOUT`, which waits for the XCTest host to boot).
_WARM_HEALTH_TIMEOUT = 5.0


def _terminate_proc(proc: subprocess.Popen[bytes] | None) -> None:
    """Terminate the runner subprocess, escalating to kill if it does not exit promptly.

    Shared by the lease-owned teardown and the pool-owned `WarmRunner.terminate`, so both stop the
    process the same way; a `None` proc (nothing spawned) is a no-op.
    """
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@dataclass
class _XcuitestWarmRunner:
    """A resident XCUITest runner the pool keeps alive across leases (BE-XXXX).

    Holds the process, its loopback port, the patched `.xctestrun` to unlink, and the driver bound to
    that port, so the pool can reuse the driver, health-check the runner before adopting it, and
    terminate it once. Implements the platform-agnostic `WarmRunner` protocol, so the pool caches it
    without importing this module's types.
    """

    actuator: str
    proc: subprocess.Popen[bytes]
    port: int
    patched_runner: Path | None
    driver: base.Driver

    def alive(self) -> bool:
        if self.proc.poll() is not None:
            return False  # the process already exited — a certain cache miss, no need to poll
        try:
            cast(base.BackendLifecycle, self.driver).await_ready(timeout=_WARM_HEALTH_TIMEOUT)
            return True
        except XcuitestChannelError:
            return False

    def terminate(self) -> None:
        _terminate_proc(self.proc)
        if self.patched_runner is not None:
            self.patched_runner.unlink(missing_ok=True)
            self.patched_runner = None


def _destination(device_type: str, udid: str) -> str:
    """Build the `xcodebuild -destination` for a Simulator or a real device (BE-0238).

    Both run the same `test-without-building`; only the platform differs — the Simulator's
    `iOS Simulator` vs a real device's `iOS`. `validated_udid` applies the shared device_id policy
    (chiefly: an id never leads with `-`, which xcodebuild would read as an option) to either id.
    """
    platform = "iOS" if device_type == "device" else "iOS Simulator"
    return f"platform={platform},id={simctl.validated_udid(udid)}"


class XcuitestEnvironment(_DeviceEnvironment):
    """The XCUITest lifecycle: simctl device prep then a resident runner on the Simulator, or the
    same runner without simctl prep on a real device (BE-0019, real-device targeting BE-0238).

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
        self._driver: base.Driver | None = None
        # Warm-runner reuse state (BE-XXXX): `_pool_owned` flips when the pool adopts the runner's
        # lifetime, so `teardown` leaves a reusable runner alive for the next lease; `_adopted` is a
        # live runner handed back for reuse (skip the spawn, relaunch only the app); `_reusable` is
        # set in `start` and is False for a real device, whose runner is never cached.
        self._pool_owned = False
        self._adopted: _XcuitestWarmRunner | None = None
        self._reusable = False

    def adopt_runner(self, handle: WarmRunner | None) -> None:
        """Take the pool's hand-off before `start` (BE-XXXX): reuse *handle* if given, and either way
        let the pool own the runner's lifetime so `teardown` no longer terminates a reusable one."""
        self._pool_owned = True
        self._adopted = cast("_XcuitestWarmRunner | None", handle)

    def running_handle(self) -> WarmRunner | None:
        """The runner handle for the pool to cache after `start` (BE-XXXX).

        The adopted handle on reuse (identity preserved), a fresh one wrapping a just-spawned runner
        on a cache miss, or `None` where no reusable runner exists (a real device, or a lease that
        never spawned one).
        """
        if self._adopted is not None:
            return self._adopted
        if not self._reusable or self._runner_proc is None or self._driver is None:
            return None
        return _XcuitestWarmRunner(
            actuator=self._actuator,
            proc=self._runner_proc,
            port=self._runner_port,
            patched_runner=self._patched_runner,
            driver=self._driver,
        )

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        ios = require_ios(eff)
        xcfg = ios.xcuitest
        device_type = xcfg.device_type if xcfg is not None else "simulator"
        # A runner is reusable only on the Simulator: a real device (BE-0238) has no simctl app
        # relaunch to hand the app over between leases, so the pool never caches its runner and its
        # lifecycle stays exactly as before (BE-XXXX).
        self._reusable = device_type != "device"

        if device_type == "device":
            # A real device is not managed through simctl: it is already powered on, its build is
            # installed out of band, and `simctl privacy` cannot reach it. The simctl-only
            # preconditions it cannot honour fail loudly here (real-device install / permissions
            # are BE-0238 Unit 2/3) rather than silently no-op'ing — determinism first.
            if pre.erase:
                raise simctl.DeviceError(
                    "erase is a simctl operation and does not apply to a real device "
                    "(xcuitest.deviceType: device)"
                )
            if ios.app_path:
                raise simctl.DeviceError(
                    "installing appPath through simctl does not apply to a real device "
                    "(xcuitest.deviceType: device); install the app and its device-build test "
                    "runner out of band"
                )
            if permissions:
                raise simctl.DeviceError(
                    "permission grants use simctl and do not apply to a real device "
                    "(xcuitest.deviceType: device)"
                )
        else:
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
                # Set permission state after install (a fresh install/erase resets TCC grants) but
                # before the runner launches the app, so a prompt never blocks it (BE-0276).
                if permissions:
                    e.apply_permissions(ios.bundle_id, permissions)
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

        # Warm reuse (BE-XXXX Unit 2): a live runner was handed back, so skip the expensive spawn and
        # hand the app over instead — the same app-only restart the in-lease `relaunch` step does
        # (terminate then launch, re-applying this scenario's launch env/args/locale). The runner
        # process is untouched; it drives whichever app is now in the foreground. Device-state
        # preconditions (`erase`) and permission grants already ran through simctl above, so a reused
        # runner never weakens the per-scenario isolation the cold path gives.
        if self._adopted is not None:
            self._runner_proc = self._adopted.proc
            self._runner_port = self._adopted.port
            self._patched_runner = self._adopted.patched_runner
            self._driver = self._adopted.driver
            e = simctl.Env(self._udid, run=self._run)
            try:
                e.terminate(ios.bundle_id)
                e.launch(ios.bundle_id, launch_args, launch_env)
                if pre.deeplink is not None:
                    e.openurl(pre.deeplink)
            except subprocess.CalledProcessError as exc:
                raise simctl.device_error(exc) from exc
            return self._driver

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
                    # Simulator vs real device (BE-0238); `_destination` validates the udid inline
                    # before it lands on the argv, the same defense-in-depth simctl/idb apply.
                    _destination(device_type, self._udid),
                ],
                env={**os.environ, **forwarded},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise simctl.DeviceError(f"failed to start xcodebuild: {exc}") from exc

        driver = backends.make_driver(self._actuator, self._udid, runner_port=self._runner_port)
        # A cold `xcodebuild test-without-building` spins up the XCTest host and launches the app
        # before the runner's server answers /health; on a loaded CI runner that first start well
        # exceeds the 10s default, so give it generous headroom (a warm start still returns at once).
        cast(base.BackendLifecycle, driver).await_ready(timeout=_RUNNER_STARTUP_TIMEOUT)
        self._driver = driver
        return driver

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        # When the pool owns a reusable runner (BE-XXXX Unit 3), leave it alive across the release so
        # the next lease can adopt it; the pool terminates it on run-set end, an actuator switch, or a
        # fault. Otherwise this instance spawned the runner and terminates it (the legacy single-lease
        # path, and a non-reusable real-device runner the pool never cached) — BE-0240.
        if not (self._pool_owned and self._reusable):
            _terminate_proc(self._runner_proc)
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
