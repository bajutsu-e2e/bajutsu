"""Resident UI Automator server channel (BE-0245): reach the on-device server over adb forward + HTTP.

The resident server (BajutsuAndroidUIAutomatorServer, PR-B) keeps one `UiAutomation` session warm and
answers `GET /source` with `UiDevice.dumpWindowHierarchy` XML, skipping the ≈ 2.4 s per-invocation
`uiautomator dump` startup. This module is the Python end of that channel: it starts the server for a
device lease, forwards a host port to it, fetches the hierarchy, and narrows the whole-screen dump to
the active window so `parse_hierarchy` produces the same Elements the dump path does. Everything above
`AdbDriver._describe()` — the transient-empty retry, `_settle`, selectors — is unchanged; only the
transport differs. A startup or channel failure raises `AdbResidentError`, which the driver catches to
fall back to `uiautomator dump` rather than reading a failed channel as an empty screen.
"""

from __future__ import annotations

import contextlib
import http.client
import logging
import math
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bajutsu import adb
from bajutsu.drivers.adb import (
    ActFn,
    ActRequest,
    AdbActUncertain,
    AdbActUnsupported,
    AdbResidentError,
    ClockFetch,
    HierarchyFetch,
    HierarchyRead,
    slice_hierarchy_root,
)

logger = logging.getLogger("bajutsu.adb.resident")

# The response header the resident server stamps `GET /source` with: the device-clock time
# (`SystemClock.uptimeMillis`) of the most recent accessibility event it had observed (BE-0332 Unit 3).
# Carried in a header so the XML body stays byte-identical to `uiautomator dump`'s, keeping
# `parse_hierarchy` and `narrow_to_active_window` unchanged.
_READ_MARK_HEADER = "X-Bajutsu-Read-Mark"

# The response header carrying each opted-in view's own `View.getZ()` (BE-0355 Unit 3), in the same
# header rather than the body and for the same reason as the mark above. Its value is
# `<key>=<z>` pairs joined by `;`, where the key names the node by what the host can recompute from
# the `<node>` it is reading — see `adb.py`'s `_native_z_key`. Absent on a server that does not
# report it and on an app that opted no view in.
_NATIVE_Z_HEADER = "X-Bajutsu-Native-Z"

# The status the resident server answers when the identity the host sent no longer names the same
# number of nodes on its own dump: the screen moved between the two resolves, so nothing was injected.
_STALE_STATUS = 409

# What an older resident server answers for a path it does not serve. Permanent for the lease, unlike a
# socket fault, so the driver latches it instead of probing again on every gesture.
_NO_ENDPOINT_STATUS = 404

# SystemUI owns the status and navigation bars — separate windows that `dumpWindowHierarchy` traverses
# but the platform `uiautomator dump` (active window only) omits. Dropping them is a uniform
# system-chrome filter, not per-app config, so the resident dump yields the same Elements as the dump
# path (prime directive 3, app-agnostic).
_SYSTEM_DECOR_PACKAGES = frozenset({"com.android.systemui"})


def narrow_to_active_window(xml: str) -> str:
    """Drop system-decor windows from a `dumpWindowHierarchy` tree so it matches the active-window dump.

    `dumpWindowHierarchy` emits one top-level `<node>` per window; `uiautomator dump` scopes to the
    active window. Removing the SystemUI status/navigation-bar windows reconciles the two so
    `parse_hierarchy` produces identical Elements. A tree with no system window (the active-window dump
    itself) passes through unchanged, and unparseable input is returned as-is so the driver's existing
    empty-tree handling still applies.

    Scope: this drops only SystemUI decor. The Android e2e lane (BE-0208) exercises the resident path
    across the showcase scenarios, including one that raises the IME (`search`); the `permission`
    scenario does not exercise this — Android pre-grants the permission
    (`demos/showcase/scenarios/permission.yaml`), so no dialog ever appears there. A permission-dialog
    window leaking past this filter is therefore not yet caught by CI; broadening the filter for that
    case is still a design decision deferred rather than guessed at here.
    """
    root = slice_hierarchy_root(xml)
    if root is None:
        return xml
    decor = [window for window in root if window.get("package") in _SYSTEM_DECOR_PACKAGES]
    if not decor:
        return xml
    for window in decor:
        root.remove(window)
    return ET.tostring(root, encoding="unicode")


def fetch_source(
    host_port: int, since: float | None = None, *, timeout: float = 5.0
) -> HierarchyRead:
    """GET the resident server's current hierarchy over the forwarded loopback host port.

    Returns the XML plus its read mark (BE-0332 Unit 3): the `X-Bajutsu-Read-Mark` header carries the
    device-clock time of the most recent accessibility event the reader had seen, so the driver can
    trust a read only once it postdates the gesture. A response without the header (an older server)
    yields a None mark, and the driver's barrier falls back to its wall-clock budget — never a failure.

    Args:
        since: The device-clock mark the read must postdate (BE-0332 Unit 4). When given, it rides on
            the request as `?since=`, and the resident server blocks until an accessibility event
            postdates it before dumping once — collapsing the host's re-poll into one round trip. None
            (a read with no gesture pending) requests the current hierarchy with no wait.

    Raises:
        AdbResidentError: the channel could not be reached or did not answer 200 — an infrastructure
            failure the driver catches to fall back to `uiautomator dump`, never a test outcome.
    """
    conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
    path = "/source" if since is None else f"/source?since={since}"
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise AdbResidentError(f"resident server returned HTTP {resp.status}")
        mark = _parse_mark(resp.getheader(_READ_MARK_HEADER))
        native_z = _parse_native_z(resp.getheader(_NATIVE_Z_HEADER))
        # A truncated/garbled body (a mid-write device server) must degrade to the dump fallback, not
        # escape past the driver's AdbResidentError-only catch — whether it surfaces as a
        # UnicodeDecodeError (garbled bytes) or an http.client.HTTPException (IncompleteRead from a
        # short body, BadStatusLine/UnknownProtocol from a malformed status line).
        return HierarchyRead(body.decode("utf-8"), mark, native_z=native_z)
    except (OSError, UnicodeDecodeError, http.client.HTTPException) as exc:
        raise AdbResidentError(f"resident channel unreachable on port {host_port}: {exc}") from exc
    finally:
        conn.close()


def act(host_port: int, request: ActRequest, *, timeout: float = 10.0) -> bool:
    """Ask the resident server to perform one gesture on an element the host already resolved.

    The element crosses as its four accessibility fields plus its ordinal among the nodes sharing them,
    never as a coordinate: the device re-finds it in a dump of its own and reads the bounds microseconds
    before injecting, closing the window in which a settling screen moves out from under a coordinate
    the host computed a round trip earlier.

    Returns:
        True when the device performed the gesture; False when it answered `409` — the identity no
        longer names the same nodes there, so the host must re-resolve rather than let a coordinate be
        guessed.

    Raises:
        AdbResidentError: the channel could not be reached, or answered anything else — including the
            `404` an older server without the endpoint returns. The driver degrades to its coordinate
            actuators, so a device that cannot serve this is never worse off than before.
    """
    fields = {
        "kind": request.kind,
        "index": str(request.index),
        "count": str(request.count),
        "rid": request.identity[0],
        "desc": request.identity[1],
        "text": request.identity[2],
        "cls": request.identity[3],
    }
    if request.since is not None:
        fields["since"] = str(request.since)
    if request.duration_ms is not None:
        fields["durationMs"] = str(request.duration_ms)
    # A longer timeout than a read: the server honors the `since` mark and settles before it injects,
    # and a press-and-hold then holds for its own duration on top of that.
    conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
    try:
        try:
            conn.request("POST", "/act?" + urllib.parse.urlencode(fields))
        except (OSError, http.client.HTTPException) as exc:
            # Nothing left the host, so nothing was injected: the caller may safely take the
            # coordinate path. This is the only fault where that is safe.
            raise AdbResidentError(
                f"resident actuation unreachable on port {host_port}: {exc}"
            ) from exc
        try:
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", "replace").strip()
        except (OSError, http.client.HTTPException) as exc:
            # The request went out, and the device injects before it answers, so the gesture may
            # already have happened. Re-actuating on the coordinate path would be a second touch.
            raise AdbActUncertain(
                f"resident actuation was sent but its reply was lost on port {host_port}: {exc}"
            ) from exc
        if resp.status == _STALE_STATUS:
            logger.debug("resident actuation reported the target moved: %s", body)
            return False
        if resp.status == _NO_ENDPOINT_STATUS:
            raise AdbActUnsupported(f"resident server has no /act endpoint (HTTP {resp.status})")
        if resp.status != 200:
            raise AdbResidentError(f"resident actuation returned HTTP {resp.status}: {body}")
        return True
    finally:
        conn.close()


def _parse_mark(raw: str | None) -> float | None:
    """The `X-Bajutsu-Read-Mark` header as a device-clock float, or None when absent/unparseable.

    A missing header (an older server without the read mark) or a malformed value degrades the barrier
    to its wall-clock budget rather than failing the read: the mark only ever tightens the wait.
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_native_z(raw: str | None) -> dict[str, float]:
    """The `X-Bajutsu-Native-Z` header as content key to measured position, empty when absent.

    A malformed pair is dropped rather than failing the read: `nativeZ` is diagnostic (BE-0355), so a
    garbled reading costs one element its position and leaves the rest of the tree intact — the same
    honest absence an app that never opted in reports.
    """
    if not raw:
        return {}
    found: dict[str, float] = {}
    for pair in raw.split(";"):
        key, sep, value = pair.rpartition("=")
        if not sep or not key:
            continue
        try:
            z = float(value)
        except ValueError:
            continue
        # `NaN` / `Infinity` parse but name no position, the same reading `native_z_from_json`
        # already refuses for a value read back off an artifact.
        if math.isfinite(z):
            found[key] = z
    return found


def fetch_clock(host_port: int, *, timeout: float = 5.0) -> float | None:
    """GET the resident server's current device clock (`SystemClock.uptimeMillis`), or None.

    Read just before a gesture (BE-0332 Unit 3) so a later read must postdate it, on the device's own
    clock, to count as caught up. Returns None on any fault or a non-200 rather than raising: the mark
    is an optimisation over the wall-clock budget, so a clock hiccup slows the barrier at worst, never
    fails a read or accepts a stale one.
    """
    conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
    try:
        conn.request("GET", "/clock")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        return float(body.decode("utf-8").strip())
    except (OSError, ValueError, UnicodeDecodeError, http.client.HTTPException):
        return None
    finally:
        conn.close()


class _Process(Protocol):
    """The slice of `subprocess.Popen` the lifecycle needs — small enough for tests to fake."""

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


Spawn = Callable[[list[str]], _Process]
Fetch = Callable[[int, float | None], HierarchyRead]
ClockProbe = Callable[[int], float | None]
ActProbe = Callable[[int, ActRequest], bool]


@dataclass(frozen=True)
class ResidentChannel:
    """The two read-side callables the driver needs from a started resident server (BE-0332 Unit 3).

    `fetch` returns the current hierarchy and its read mark, blocking until the read postdates the mark
    it is passed (BE-0332 Unit 4); `clock` returns the device's current clock so the driver can anchor
    its read-lag barrier before a gesture; `act` performs a gesture on the device against an element the
    host already resolved, so no coordinate crosses. All three close over the lease's forwarded host
    port.
    """

    fetch: HierarchyFetch
    clock: ClockFetch
    act: ActFn


# APK build outputs of `make -C BajutsuAndroidUIAutomatorServer build` (gitignored; the paths gradle
# writes).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVER_APK = (
    _REPO_ROOT / "BajutsuAndroidUIAutomatorServer/server/build/outputs/apk/debug/server-debug.apk"
)
_TEST_APK = (
    _REPO_ROOT / "BajutsuAndroidUIAutomatorServer/server/build/outputs/apk/androidTest/debug"
    "/server-debug-androidTest.apk"
)


def server_apks_built(server_apk: Path = _SERVER_APK, test_apk: Path = _TEST_APK) -> bool:
    """True when both resident-server APKs exist, so the resident read channel can start.

    The default-on gate reads this to pick the resident channel over `uiautomator dump` only when
    `make -C BajutsuAndroidUIAutomatorServer build` has produced both outputs; a fresh clone that never built
    them (the build outputs are gitignored) falls back to the dump path untouched.
    """
    return server_apk.exists() and test_apk.exists()


def _default_spawn(argv: list[str]) -> _Process:
    # The instrumentation blocks (serve() never returns), so it runs in the background for the lease;
    # its output is drained to DEVNULL so a full pipe never wedges it.
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        fetch: Fetch = fetch_source,
        clock: ClockProbe = fetch_clock,
        act_probe: ActProbe = act,
        server_apk: Path = _SERVER_APK,
        test_apk: Path = _TEST_APK,
    ) -> None:
        self._serial = adb.checked_serial(serial)
        self._run = run
        self._spawn = spawn
        self._fetch = fetch
        self._clock = clock
        self._act = act_probe
        self._server_apk = server_apk
        self._test_apk = test_apk
        self._proc: _Process | None = None
        self._host_port: int | None = None

    def start(self) -> ResidentChannel:
        if not self._server_apk.exists() or not self._test_apk.exists():
            raise AdbResidentError(
                f"resident server APKs not built ({self._server_apk}); run "
                "`make -C BajutsuAndroidUIAutomatorServer build`"
            )
        try:
            # Clear both first. A device carrying an older pair fails `install -r` outright when the
            # signing key differs, and where it succeeds it can leave the instrumentation and the
            # server disagreeing about which endpoints exist — a `/act` that 404s against a server
            # that has one, which is the confusing half of this channel's failure modes.
            for package in (adb.RESIDENT_TEST_PACKAGE, adb.RESIDENT_SERVER_PACKAGE):
                with contextlib.suppress(subprocess.CalledProcessError, OSError):
                    self._run(adb.uninstall_cmd(self._serial, package))
            self._run(adb.install_cmd(self._serial, str(self._server_apk)))
            self._run(adb.install_cmd(self._serial, str(self._test_apk)))
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
                narrowed = narrow_to_active_window(read.text)
                # Only when narrowing actually changed something: an active-window dump with no system
                # decor to strip passes through unchanged, and carrying an identical `raw` alongside
                # `text` would make every `rawTree` capture write two copies of the same body.
                raw = read.text if narrowed != read.text else None
                return HierarchyRead(narrowed, read.mark, raw=raw, native_z=read.native_z)
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

        def act_on_device(request: ActRequest) -> bool:
            # Unlike `fetch`, a fault here does not tear the channel down. The reads are still good —
            # an older server answers 404 for this path alone — and the driver's own degrade puts the
            # gesture back on the coordinate actuators. Killing a working read channel over a missing
            # actuation endpoint would trade a small regression for a large one.
            return self._act(port, request)

        return ResidentChannel(fetch, clock, act_on_device)

    def stop(self) -> None:
        """Kill the instrumentation and remove the forward; safe to call on a partial start."""
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


def _parse_forward_port(stdout: str) -> int:
    """The host port `adb forward tcp:0 …` chose, printed on stdout.

    Read as the last bare-number line rather than the whole output, because adb prepends its own
    chatter whenever the invocation happens to be the one that starts the server ("* daemon not
    running; starting now at tcp:5037", "* daemon started successfully"). Parsing the whole string
    would raise on exactly those runs, and a failed forward takes the resident channel down with it —
    the lease then reads through `uiautomator dump`, which carries no device mark, so every read-lag
    barrier silently falls back to spending its full budget. Tolerating the banner keeps a cosmetic
    line from costing the channel.
    """
    ports = [line.strip() for line in stdout.splitlines() if line.strip().isdigit()]
    if not ports:
        raise AdbResidentError(f"adb forward did not report a host port: {stdout!r}")
    return int(ports[-1])
