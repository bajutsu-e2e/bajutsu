"""Tests for the resident UI Automator server channel (BE-0245).

The resident channel reaches an on-device server over `adb forward` + HTTP instead of paying
`uiautomator dump`'s per-invocation startup. These cover the three deterministic, device-free
pieces: narrowing the server's whole-screen dump to the active window so `parse_hierarchy` yields the
same Elements as the dump path, the HTTP client (against a real loopback server, no mock), and the
server lifecycle over an injected `run`. Real on-device verification is a later slice (PR-D).
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from bajutsu import adb, adb_resident
from bajutsu.drivers.adb import AdbResidentError, parse_hierarchy

# One app window (a Views button) — the content the platform `uiautomator dump` returns.
_APP_WINDOW = """  <node index="0" class="android.widget.FrameLayout" \
package="com.bajutsu.showcase.android.views" bounds="[0,0][1080,2400]">
    <node index="0" text="送信" resource-id="stable.submit" class="android.widget.Button" \
content-desc="" enabled="true" bounds="[0,200][200,300]" />
  </node>"""

# The SystemUI status bar — a separate window `dumpWindowHierarchy` traverses but `uiautomator dump`
# omits (its clock/wifi/battery nodes are the ≈29 extra the resident dump must shed).
_SYSTEMUI_WINDOW = """  <node index="0" class="android.widget.FrameLayout" \
package="com.android.systemui" bounds="[0,0][1080,80]">
    <node index="0" text="12:00" resource-id="com.android.systemui:id/clock" \
class="android.widget.TextView" bounds="[0,0][100,80]" />
  </node>"""

# `dumpWindowHierarchy` output: multiple window roots directly under <hierarchy>.
_MULTI_WINDOW = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    f'<hierarchy rotation="0">\n{_SYSTEMUI_WINDOW}\n{_APP_WINDOW}\n</hierarchy>'
)
# `uiautomator dump` output: the active window only.
_APP_ONLY = (
    "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
    f'<hierarchy rotation="0">\n{_APP_WINDOW}\n</hierarchy>'
)


def test_narrow_matches_the_active_window_dump() -> None:
    # The whole point of PR-C's equivalence work: after narrowing, the resident dump parses to exactly
    # the Elements the `uiautomator dump` path produces — the SystemUI window is gone.
    narrowed = adb_resident.narrow_to_active_window(_MULTI_WINDOW)
    assert parse_hierarchy(narrowed) == parse_hierarchy(_APP_ONLY)
    # Guard the test itself: the two dumps genuinely differ before narrowing.
    assert parse_hierarchy(_MULTI_WINDOW) != parse_hierarchy(_APP_ONLY)


def test_narrow_leaves_the_active_window_dump_untouched() -> None:
    # A dump with no system window (already the active window, e.g. the fallback path fed back through)
    # passes through so the two transports converge on identical Elements.
    assert parse_hierarchy(adb_resident.narrow_to_active_window(_APP_ONLY)) == parse_hierarchy(
        _APP_ONLY
    )


def test_narrow_returns_unparseable_input_unchanged() -> None:
    # Garbage/mid-transition text is handed straight to parse_hierarchy, which yields [] as before —
    # narrowing never masks a bad read.
    assert adb_resident.narrow_to_active_window("null root node") == "null root node"


class _SourceHandler(http.server.BaseHTTPRequestHandler):
    body = _MULTI_WINDOW
    status = 200

    def do_GET(self) -> None:
        if self.path != "/source":
            self.send_error(404)
            return
        payload = self.body.encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass  # keep the test output quiet


def _serve_once(status: int = 200) -> tuple[int, http.server.HTTPServer]:
    _SourceHandler.status = status
    server = http.server.HTTPServer(("127.0.0.1", 0), _SourceHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port, server


def test_fetch_source_reads_the_hierarchy_over_http() -> None:
    port, server = _serve_once()
    try:
        assert adb_resident.fetch_source(port) == _MULTI_WINDOW
    finally:
        server.shutdown()


def test_fetch_source_raises_on_unreachable_channel() -> None:
    # A closed port is an infrastructure failure, surfaced as AdbResidentError (which the driver
    # catches to fall back to the dump) rather than a bare OSError or a masked empty read.
    port, server = _serve_once()
    server.shutdown()  # nothing is listening now
    with pytest.raises(AdbResidentError):
        adb_resident.fetch_source(port)


def test_fetch_source_raises_on_non_200() -> None:
    port, server = _serve_once(status=500)
    try:
        with pytest.raises(AdbResidentError):
            adb_resident.fetch_source(port)
    finally:
        server.shutdown()


class _TruncatedHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # Promise more bytes than we write, then close: reading the response raises
        # http.client.IncompleteRead — the mid-write device server the fetch must degrade on.
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", "1000")
        self.end_headers()
        self.wfile.write(b"<hierarchy>")  # far fewer than the advertised 1000 bytes

    def log_message(self, *args: object) -> None:
        pass


def test_fetch_source_raises_on_a_truncated_response() -> None:
    # A mid-write server whose body is shorter than its Content-Length surfaces as
    # http.client.IncompleteRead; fetch_source must normalize it to AdbResidentError (not let it
    # escape past the driver's AdbResidentError-only catch) so the driver falls back to the dump.
    server = http.server.HTTPServer(("127.0.0.1", 0), _TruncatedHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(AdbResidentError):
            adb_resident.fetch_source(server.server_port)
    finally:
        server.shutdown()


class _FakeProc:
    # A normally-terminating process: terminate() (then the wait that reaps it) brings it down, so
    # poll() reports an exit code afterwards and stop()'s kill-escalation branch is NOT taken. A stuck
    # process is modelled by _StuckProc, which pins poll() at None.
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.waited = False
        self._exit: int | None = None

    def terminate(self) -> None:
        self.terminated = True
        self._exit = -15  # SIGTERM: the process is down now, so poll() stops returning None

    def kill(self) -> None:
        self.killed = True
        self._exit = -9  # SIGKILL

    def poll(self) -> int | None:
        return self._exit

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self._exit if self._exit is not None else 0


def _apks(tmp_path: Path) -> tuple[Path, Path]:
    server_apk = tmp_path / "server-debug.apk"
    test_apk = tmp_path / "server-debug-androidTest.apk"
    server_apk.write_bytes(b"apk")
    test_apk.write_bytes(b"apk")
    return server_apk, test_apk


def test_start_installs_forwards_and_returns_a_working_fetch(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    proc = _FakeProc()
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: proc,
        fetch=lambda port: _MULTI_WINDOW,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    fetch = srv.start()
    # Both APKs installed, the blocking instrumentation spawned, a host port forwarded.
    assert calls[0] == adb.install_cmd("U", str(server_apk))
    assert calls[1] == adb.install_cmd("U", str(test_apk))
    assert calls[2] == adb.forward_cmd("U")
    # The returned fetch reads over the channel and narrows to the active window (no SystemUI window).
    assert parse_hierarchy(fetch()) == parse_hierarchy(_APP_ONLY)


def test_stop_removes_the_forward_and_kills_the_instrumentation(tmp_path: Path) -> None:
    teardown: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "--remove" in args or "force-stop" in args:
            teardown.append(args)
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    proc = _FakeProc()
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: proc,
        fetch=lambda port: _MULTI_WINDOW,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    srv.start()
    srv.stop()
    assert proc.terminated
    # terminate() sufficed (poll() reports the process down after the reap wait), so stop() does NOT
    # escalate to kill() — the stuck→kill path is exercised separately below.
    assert not proc.killed
    assert adb.forward_remove_cmd("U", 41000) in teardown
    # The device-side instrumentation is force-stopped too, so no resident @Test outlives the lease.
    assert adb.force_stop_cmd("U", adb.RESIDENT_SERVER_PACKAGE) in teardown


def test_start_raises_when_the_apks_are_not_built(tmp_path: Path) -> None:
    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "",
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port: _MULTI_WINDOW,
        server_apk=tmp_path / "missing.apk",
        test_apk=tmp_path / "missing-test.apk",
    )
    with pytest.raises(AdbResidentError, match="not built"):
        srv.start()


def test_start_raises_when_the_instrumentation_exits_before_serving(tmp_path: Path) -> None:
    # If `am instrument` dies before the socket answers, waiting to the deadline is pointless — the
    # exited process is detected and start fails fast so the caller falls back to the dump path.
    proc = _FakeProc()
    proc._exit = 1
    server_apk, test_apk = _apks(tmp_path)

    def fetch(port: int) -> str:
        raise AdbResidentError("not up yet")

    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "41000\n" if "forward" in args else "",
        spawn=lambda argv: proc,
        fetch=fetch,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    with pytest.raises(AdbResidentError, match="exited"):
        srv.start()


def test_start_tears_down_when_the_forward_port_cannot_be_parsed(tmp_path: Path) -> None:
    # `adb forward` printing something that isn't a port is a start failure — but the instrumentation
    # is already spawned and the forward already established, so start() must tear both down rather
    # than leak them (its except tuple must catch the parse error, not let it escape).
    teardown: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "--remove" in args or "force-stop" in args:
            teardown.append(args)
        # A garbage stdout for the forward call so _parse_forward_port raises.
        return "not-a-port\n" if "forward" in args and "--remove" not in args else ""

    proc = _FakeProc()
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: proc,
        fetch=lambda port: _MULTI_WINDOW,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    with pytest.raises(AdbResidentError, match="could not start"):
        srv.start()
    # The spawned instrumentation was torn down; the forward was never parsed so nothing to remove.
    assert proc.terminated
    assert adb.force_stop_cmd("U", adb.RESIDENT_SERVER_PACKAGE) in teardown


def test_start_fails_when_the_server_never_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other terminal _await_ready branch: the process stays alive but the socket never answers, so
    # the deadline elapses. Shrink the timeout/poll so the branch runs without a real 20s wait.
    monkeypatch.setattr(adb_resident.ResidentServer, "_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(adb_resident.ResidentServer, "_READY_POLL_S", 0.0)
    proc = _FakeProc()  # poll() stays None: the process is up but not serving
    server_apk, test_apk = _apks(tmp_path)

    def fetch(port: int) -> str:
        raise AdbResidentError("not up yet")

    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "41000\n" if "forward" in args else "",
        spawn=lambda argv: proc,
        fetch=fetch,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    with pytest.raises(AdbResidentError, match="did not answer within"):
        srv.start()
    # The deadline branch tears the forward down on its way out.
    assert srv._host_port is None


def test_stop_escalates_to_kill_when_terminate_does_not_reap(tmp_path: Path) -> None:
    # If terminate() leaves the process alive (poll() still None after the wait), stop() must escalate
    # to kill() so a stuck adb client is actually reaped, not silently dropped.
    class _StuckProc(_FakeProc):
        def poll(self) -> int | None:
            return None  # never comes down on terminate()

    proc = _StuckProc()
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "41000\n" if "forward" in args and "--remove" not in args else "",
        spawn=lambda argv: proc,
        fetch=lambda port: _MULTI_WINDOW,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    srv.start()
    srv.stop()
    assert proc.terminated
    assert proc.killed
