"""Resident UI Automator server channel (BE-0245): reach the on-device server over adb forward + HTTP.

The resident server (BajutsuAndroidServer, PR-B) keeps one `UiAutomation` session warm and answers
`GET /source` with `UiDevice.dumpWindowHierarchy` XML, skipping the ≈ 2.4 s per-invocation
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
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from bajutsu import adb
from bajutsu.drivers.adb import AdbResidentError, HierarchyFetch

logger = logging.getLogger("bajutsu.adb.resident")

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

    Scope: this drops only SystemUI decor (the one window difference PR-C targets). Other non-app
    windows a system dialog might add — a permission dialog (`android`), the IME — are left for PR-D's
    on-device verification to catalogue against the dump path; broadening the filter is a design
    decision deferred to that slice rather than guessed at here.
    """
    start = xml.find("<hierarchy")
    end = xml.rfind("</hierarchy>")
    if start == -1 or end == -1:
        return xml
    try:
        # UI Automator's own output over our channel — an attribute-only tree, not attacker XML.
        root = ET.fromstring(xml[start : end + len("</hierarchy>")])  # noqa: S314
    except ET.ParseError:
        return xml
    decor = [window for window in root if window.get("package") in _SYSTEM_DECOR_PACKAGES]
    if not decor:
        return xml
    for window in decor:
        root.remove(window)
    return ET.tostring(root, encoding="unicode")


def fetch_source(host_port: int, *, timeout: float = 5.0) -> str:
    """GET the resident server's current hierarchy XML over the forwarded loopback host port.

    Raises:
        AdbResidentError: the channel could not be reached or did not answer 200 — an infrastructure
            failure the driver catches to fall back to `uiautomator dump`, never a test outcome.
    """
    conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
    try:
        conn.request("GET", "/source")
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise AdbResidentError(f"resident server returned HTTP {resp.status}")
        # A truncated/garbled body (a mid-write device server) must degrade to the dump fallback, not
        # escape past the driver's AdbResidentError-only catch — whether it surfaces as a
        # UnicodeDecodeError (garbled bytes) or an http.client.HTTPException (IncompleteRead from a
        # short body, BadStatusLine/UnknownProtocol from a malformed status line).
        return body.decode("utf-8")
    except (OSError, UnicodeDecodeError, http.client.HTTPException) as exc:
        raise AdbResidentError(f"resident channel unreachable on port {host_port}: {exc}") from exc
    finally:
        conn.close()


class _Process(Protocol):
    """The slice of `subprocess.Popen` the lifecycle needs — small enough for tests to fake."""

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


Spawn = Callable[[list[str]], _Process]
Fetch = Callable[[int], str]

# APK build outputs of `make -C BajutsuAndroidServer build` (gitignored; the paths gradle writes).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVER_APK = _REPO_ROOT / "BajutsuAndroidServer/server/build/outputs/apk/debug/server-debug.apk"
_TEST_APK = (
    _REPO_ROOT / "BajutsuAndroidServer/server/build/outputs/apk/androidTest/debug"
    "/server-debug-androidTest.apk"
)


def _default_spawn(argv: list[str]) -> _Process:
    # The instrumentation blocks (serve() never returns), so it runs in the background for the lease;
    # its output is drained to DEVNULL so a full pipe never wedges it.
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ResidentServer:
    """The on-device resident server for one device lease (BE-0245).

    `start` installs the server APKs, launches the blocking instrumentation, forwards a host port, and
    waits — a bounded connect retry, a condition wait with no fixed sleep — until the socket answers,
    returning a `HierarchyFetch` the driver calls per read. `stop` kills the instrumentation and
    removes the forward. Any startup failure raises `AdbResidentError`, so the caller degrades to
    `uiautomator dump` rather than failing the run.
    """

    _READY_TIMEOUT_S = 20.0  # generous: instrumentation install + UiAutomation session bring-up
    _READY_POLL_S = 0.2

    def __init__(
        self,
        serial: str,
        *,
        run: adb.RunFn = adb._real_run,
        spawn: Spawn = _default_spawn,
        fetch: Fetch = fetch_source,
        server_apk: Path = _SERVER_APK,
        test_apk: Path = _TEST_APK,
    ) -> None:
        self._serial = adb._checked_serial(serial)
        self._run = run
        self._spawn = spawn
        self._fetch = fetch
        self._server_apk = server_apk
        self._test_apk = test_apk
        self._proc: _Process | None = None
        self._host_port: int | None = None

    def start(self) -> HierarchyFetch:
        if not self._server_apk.exists() or not self._test_apk.exists():
            raise AdbResidentError(
                f"resident server APKs not built ({self._server_apk}); run "
                "`make -C BajutsuAndroidServer build`"
            )
        try:
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
        return lambda: narrow_to_active_window(self._fetch(port))

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
                self._fetch(self._host_port)
                return
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


def _parse_forward_port(stdout: str) -> int:
    """The host port `adb forward tcp:0 …` chose, printed on stdout."""
    text = stdout.strip()
    try:
        return int(text)
    except ValueError as exc:
        raise AdbResidentError(f"adb forward did not report a host port: {stdout!r}") from exc
