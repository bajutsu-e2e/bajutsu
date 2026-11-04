"""The on-device resident UiAutomator server for one device lease (BE-0245)."""

from __future__ import annotations

import contextlib
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from bajutsu.common.backend_cli import adb
from bajutsu.common.drivers.adb import ActOutcome, ActRequest, AdbResidentError, HierarchyRead

from ._functions import (
    _apk_digest,
    _default_spawn,
    _parse_forward_port,
    act,
    fetch_clock,
    fetch_source,
    narrowed_root,
    type_text,
)
from ._process import _Process
from ._shared import _SERVER_APK, _TEST_APK, logger
from .keepalive import Keepalive
from .resident_channel import ResidentChannel

Spawn = Callable[[list[str]], _Process]
# The three per-port calls a lease makes, as the shapes a test can substitute. They take the port and
# nothing else the caller has to know about: whichever connection carries them, and whether the read
# asks for `nativeZ`, are bound by `ResidentServer` when it builds its own defaults.
Fetch = Callable[[int, float | None], HierarchyRead]
ClockProbe = Callable[[int], float | None]
ActProbe = Callable[[int, ActRequest], ActOutcome]
TypeProbe = Callable[[int, str], None]


class ResidentServer:
    """The on-device resident server for one device lease (BE-0245).

    `start` installs the server APKs, launches the blocking instrumentation, forwards a host port, and
    waits — a bounded connect retry, a condition wait with no fixed sleep — until the socket answers,
    returning a `ResidentChannel` (a per-read hierarchy fetch and a device-clock probe) the driver
    calls per read and per gesture. `stop` kills the instrumentation and removes the forward. Any
    startup failure raises `AdbResidentError`, so the caller degrades to `uiautomator dump` rather than
    failing the run.
    """

    _READY_TIMEOUT_S = 20.0  # generous: instrumentation install + UiAutomation session bring-up
    _READY_POLL_S = 0.2

    def __init__(
        self,
        serial: str,
        *,
        run: adb.RunFn = adb.real_run,
        spawn: Spawn = _default_spawn,
        fetch: Fetch | None = None,
        clock: ClockProbe | None = None,
        act_probe: ActProbe | None = None,
        type_probe: TypeProbe | None = None,
        server_apk: Path = _SERVER_APK,
        test_apk: Path = _TEST_APK,
        installed: dict[str, tuple[str, str]] | None = None,
        native_z: bool = False,
    ) -> None:
        self._serial = adb.checked_serial(serial)
        self._run = run
        self._spawn = spawn
        # One connection for the lease, shared by all three calls below (BE-0407 unit 21). Built here
        # rather than in `start`, so a `stop`-then-`start` on the same server reuses the record and a
        # test that substitutes a call gets no socket at all.
        self._keepalive = Keepalive()
        # Whether this target asked for the `nativeZ` reading (BE-0407 unit 18). Kept, not just
        # closed over, because `start` consults it again to decide whether a gesture's own tree can
        # stand in for a read — a seeded tree carries no `nativeZ` header.
        self._native_z = native_z
        # Defaulted here rather than in the signature because the real implementations need two
        # things only this object knows — its connection, and whether this target asked for
        # `nativeZ` — while a substituted call takes the bare `(port, …)` shape and neither.
        self._fetch = fetch or (
            lambda port, since: fetch_source(
                port, since, native_z=native_z, keepalive=self._keepalive
            )
        )
        self._clock = clock or (lambda port: fetch_clock(port, keepalive=self._keepalive))
        # `tree=0` when this target asked for `nativeZ`: the reply cannot carry that reading, so a
        # tree seeded from it would report every element's position as absent — indistinguishable
        # from an app that opted no view in, the one confusion BE-0355 works hardest to avoid. Said
        # on the *request* rather than dropped from the reply, so the device does not build and ship
        # a settled dump the host will discard, which would make unit 19 a per-gesture regression on
        # exactly the targets unit 18 exists for.
        self._act = act_probe or (
            lambda port, request: act(
                port, request, keepalive=self._keepalive, want_tree=not native_z
            )
        )
        self._type_text = type_probe or (
            lambda port, text: type_text(port, text, keepalive=self._keepalive)
        )
        self._server_apk = server_apk
        self._test_apk = test_apk
        # Which APK pair this run already put on which device (BE-0407 unit 22). Owned by the caller
        # because a `ResidentServer` lives for one lease while the pair survives the whole run; None
        # (the default) keeps the reinstall-every-lease behavior every other caller had.
        self._installed = installed
        self._proc: _Process | None = None
        self._host_port: int | None = None

    def start(self) -> ResidentChannel:
        if not self._server_apk.exists() or not self._test_apk.exists():
            raise AdbResidentError(
                f"resident server APKs not built ({self._server_apk}); run "
                "`make -C BajutsuAndroidUIAutomatorServer build`"
            )
        try:
            self._install_apks()
            self._proc = self._spawn(adb.instrument_cmd(self._serial))
            self._host_port = _parse_forward_port(self._run(adb.forward_cmd(self._serial)))
        except (subprocess.CalledProcessError, OSError, AdbResidentError) as exc:
            # AdbResidentError included so an unparseable forward port (raised by _parse_forward_port
            # on the line above) still tears down the already-spawned instrumentation and forward
            # rather than leaking them — start() is the only place that can clean up, since the caller
            # never sees the ResidentServer when start() raises.
            self.stop()
            raise AdbResidentError(f"could not start the resident server: {exc}") from exc
        self._await_ready()
        # Capture the port (not self._host_port, which stop() clears): after stop() the fetch raises
        # AdbResidentError, which the driver latches into its dump fallback — a clean degrade.
        port = self._host_port

        def fetch(since: float | None) -> HierarchyRead:
            try:
                read = self._fetch(port, since)
                # The device's reply travels on as `text`, untouched; what the driver parses is the
                # tree narrowing already built from it. `narrowed` is what tells a `rawTree` capture
                # the two differ — an active-window dump with no system decor to strip would otherwise
                # have it write two copies of the same body.
                root, narrowed = narrowed_root(read.text)
                return HierarchyRead(
                    read.text, read.mark, native_z=read.native_z, root=root, narrowed=narrowed
                )
            except AdbResidentError:
                # Stop the resident server before the driver degrades to `uiautomator dump`. A read
                # fault is usually a wedged-but-alive instrumentation — a read that outran the socket
                # timeout, not a dead process — and it still holds the device's single UiAutomation
                # session. A fallback dump connects its own (BE-0245: the dump path "spins up a fresh
                # instrumentation, connects a UiAutomation"), so while the resident server lives the
                # dump reads an empty tree for the rest of the lease, breaking every later read.
                # Tearing it down here releases that session, making the fallback a clean degrade
                # rather than one poisoned by the very server it replaces. stop() is idempotent, so
                # the environment's own end-of-lease stop() stays a safe no-op.
                self.stop()
                raise

        def clock() -> float | None:
            # The device-clock probe the driver takes before each gesture (BE-0332 Unit 3). Already
            # non-raising (None on any fault), so unlike `fetch` it never tears the channel down: a
            # missing mark only drops the barrier back to its wall-clock budget for that one gesture,
            # and a genuine channel death still surfaces through the next `fetch`.
            return self._clock(port)

        def act_on_device(request: ActRequest) -> ActOutcome:
            # Unlike `fetch`, a fault here does not tear the channel down. The reads are still good —
            # an older server answers 404 for this path alone — and the driver's own degrade puts the
            # gesture back on the coordinate actuators. Killing a working read channel over a missing
            # actuation endpoint would trade a small regression for a large one.
            return self._act(port, request)

        def type_on_device(text: str) -> None:
            # Like `act_on_device`, a fault here leaves the read channel up: it says nothing about
            # the reads, and the driver fails this one step on it.
            self._type_text(port, text)

        return ResidentChannel(fetch, clock, act_on_device, type_on_device)

    def _install_apks(self) -> None:
        """Put this run's resident-server pair on the device, unless it is already the pair there.

        The install is the dominant cost of starting a lease — an uninstall of both packages followed
        by two `install -r` calls, ~6-12 s on a cold emulator — and every lease after the first put
        the very same bytes back (BE-0407 unit 22). Skipping it needs two things to hold together:
        that *this run* installed this exact pair on this serial (the digests, which pin the signing
        key too, since identical bytes cannot be signed differently), and that both packages are
        still on the device. The second is checked rather than assumed, so a package removed out of
        band brings the install back rather than leaving the lease to fail on a missing endpoint.
        What it does not see is a package *replaced* out of band — another checkout's
        `gradlew installDebug`, say — which `pm path` reports as present, so the skip would take it
        with the wrong build. Contrived enough to leave uncovered, but not covered.
        """
        wanted = (_apk_digest(self._server_apk), _apk_digest(self._test_apk))
        if (
            self._installed is not None
            and self._installed.get(self._serial) == wanted
            and self._packages_installed()
        ):
            logger.debug("resident APKs already installed on %s; skipping reinstall", self._serial)
            return
        # Clear both first. A device carrying an older pair fails `install -r` outright when the
        # signing key differs, and where it succeeds it can leave the instrumentation and the
        # server disagreeing about which endpoints exist — a `/act` that 404s against a server
        # that has one, which is the confusing half of this channel's failure modes.
        if self._installed is not None:
            # Dropped before the uninstall, not after the install: a failure anywhere below must not
            # leave a record claiming a pair is installed when the device no longer carries it.
            self._installed.pop(self._serial, None)
        for package in (adb.RESIDENT_TEST_PACKAGE, adb.RESIDENT_SERVER_PACKAGE):
            with contextlib.suppress(subprocess.CalledProcessError, OSError):
                self._run(adb.uninstall_cmd(self._serial, package))
        self._run(adb.install_cmd(self._serial, str(self._server_apk)))
        self._run(adb.install_cmd(self._serial, str(self._test_apk)))
        if self._installed is not None:
            self._installed[self._serial] = wanted

    def _packages_installed(self) -> bool:
        """Whether the device still carries both resident packages; False on any doubt.

        One `pm path` per package — tens of milliseconds against the seconds the skip saves. A fault
        reads as "not installed", so an unanswerable device reinstalls rather than starting a server
        that may not be there.
        """
        try:
            for package in (adb.RESIDENT_SERVER_PACKAGE, adb.RESIDENT_TEST_PACKAGE):
                # The `package:` prefix `pm path` actually emits, not merely non-empty output: an
                # adb error line or a `pm` diagnostic would otherwise read as "installed" and skip
                # an install the device needs, leaving the lease to fail on a missing endpoint.
                if not self._run(adb.package_path_cmd(self._serial, package)).startswith(
                    "package:"
                ):
                    logger.debug(
                        "resident package %s not reported installed on %s", package, self._serial
                    )
                    return False
        except (subprocess.CalledProcessError, OSError) as exc:
            # Said out loud, because the consequence is silent: a device that cannot answer this
            # reinstalls both APKs on every lease, losing the seconds unit 22 exists to save.
            logger.debug(
                "could not ask %s about its packages (%s); reinstalling", self._serial, exc
            )
            return False
        return True

    def stop(self) -> None:
        """Kill the instrumentation and remove the forward; safe to call on a partial start."""
        # Before the forward goes away, so the kept connection is closed rather than left pointing at
        # a port `adb forward --remove` is about to retire (BE-0407 unit 21).
        self._keepalive.discard()
        if self._proc is not None:
            with contextlib.suppress(OSError):
                self._proc.terminate()
            # Reap the terminated adb client so a long-lived `serve` process does not accumulate a
            # zombie per lease (terminate() alone leaves the child unwaited on POSIX). If terminate()
            # does not bring it down in time, escalate to kill() so a stuck process is still reaped —
            # otherwise the guarantee this wait exists for would silently not hold.
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                self._proc.wait(timeout=5)
            if self._proc.poll() is None:
                with contextlib.suppress(OSError):
                    self._proc.kill()
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    self._proc.wait(timeout=5)
            self._proc = None
            # Killing the local adb client does not reliably stop the device-side instrumentation, so
            # force-stop its package too — otherwise a resident @Test could outlive the lease.
            try:
                self._run(adb.force_stop_cmd(self._serial, adb.RESIDENT_SERVER_PACKAGE))
            except (subprocess.CalledProcessError, OSError) as exc:
                logger.debug("resident force-stop failed (%s); instrumentation may linger", exc)
        if self._host_port is not None:
            with contextlib.suppress(subprocess.CalledProcessError, OSError):
                self._run(adb.forward_remove_cmd(self._serial, self._host_port))
            self._host_port = None

    def _await_ready(self) -> None:
        # A bounded condition wait, not a fixed sleep: poll until the socket answers. Before the server
        # binds, the connect is refused immediately (fast), so the fetch timeout only applies once the
        # server is essentially up — the effective ceiling stays ~_READY_TIMEOUT_S.
        assert self._host_port is not None
        deadline = time.monotonic() + self._READY_TIMEOUT_S
        while True:
            try:
                self._fetch(self._host_port, None)  # a readiness probe waits past no mark
            except AdbResidentError:
                # The polled fetch failing is the expected not-up-yet signal, not a cause to chain;
                # these raises are the terminal startup verdict, so break the exception chain.
                if self._proc is not None and self._proc.poll() is not None:
                    self.stop()
                    raise AdbResidentError(
                        "resident instrumentation exited before serving"
                    ) from None
                if time.monotonic() >= deadline:
                    self.stop()
                    raise AdbResidentError(
                        f"resident server did not answer within {self._READY_TIMEOUT_S:.0f}s"
                    ) from None
                time.sleep(self._READY_POLL_S)
            else:
                return
