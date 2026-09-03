"""Tests for the resident UI Automator server channel (BE-0245).

The resident channel reaches an on-device server over `adb forward` + HTTP instead of paying
`uiautomator dump`'s per-invocation startup. These cover the three deterministic, device-free
pieces: narrowing the server's whole-screen dump to the active window so `parse_hierarchy` yields the
same Elements as the dump path, the HTTP client (against a real loopback server, no mock), and the
server lifecycle over an injected `run`. Real on-device verification is a later slice (PR-D).
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.parse
from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb, adb_resident
from bajutsu.common.drivers.adb import (
    ActOutcome,
    ActRequest,
    AdbActUncertain,
    AdbResidentError,
    HierarchyRead,
    parse_hierarchy,
)

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


# A second non-SystemUI window (a permission dialog, say) — same shape as `_APP_WINDOW` but its own
# package, simulating the multi-window-mid-transition case the function's own docstring names as an
# unaddressed gap.
_DIALOG_WINDOW = """  <node index="0" class="android.widget.FrameLayout" \
package="com.android.permissioncontroller" bounds="[100,800][980,1600]">
    <node index="0" text="許可" resource-id="permission_allow_button" \
class="android.widget.Button" content-desc="" enabled="true" bounds="[400,1400][680,1500]" />
  </node>"""


def test_narrow_characterizes_two_simultaneous_non_systemui_windows() -> None:
    # Characterization, not a fix: `narrow_to_active_window` filters SystemUI decor only, by package
    # name — it has no notion of "the one active window" among several non-SystemUI windows. Both
    # survive narrowing and both windows' nodes appear in the parsed tree, exactly as `dumpWindowHierarchy`
    # produced them; a real `uiautomator dump` (active-window-only) would show just one. This fixes the
    # current behavior in place so a change to it shows up as a diff here rather than silently, and
    # documents the gap the module's own docstring names but does not yet close.
    multi = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n"
        f'<hierarchy rotation="0">\n{_SYSTEMUI_WINDOW}\n{_APP_WINDOW}\n{_DIALOG_WINDOW}\n</hierarchy>'
    )
    narrowed = adb_resident.narrow_to_active_window(multi)
    els = parse_hierarchy(narrowed)
    # SystemUI is gone, but both the app window and the dialog window remain — neither was chosen
    # over the other.
    assert any(e["identifier"] == "stable.submit" for e in els)
    assert any(e["identifier"] == "permission_allow_button" for e in els)
    assert not any((e["label"] or "") == "12:00" for e in els)  # the SystemUI clock, gone


class _SourceHandler(http.server.BaseHTTPRequestHandler):
    body = _MULTI_WINDOW
    status = 200
    mark: str | None = None  # the X-Bajutsu-Read-Mark header value, when set (BE-0332 Unit 3)
    clock = "12345"  # what GET /clock answers, a device-clock reading
    last_source_path: str | None = None  # the full GET /source target the client last sent

    act_status = 200  # what POST /act answers
    last_act_path: str | None = None  # the full POST /act target the client last sent
    act_drop_reply = False  # accept the request, then hang up without answering
    # The X-Bajutsu-Act-Publish header value, when set (BE-0339 Unit 5). None is the older server that
    # never waited for its gesture to publish, and the device that waited and saw nothing.
    act_publish: str | None = None

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/act":
            self.send_error(404)
            return
        type(self).last_act_path = self.path
        if self.act_drop_reply:
            self.close_connection = True
            self.wfile.close()
            return
        self.send_response(self.act_status)
        self.send_header("Content-Length", "0")
        if self.act_publish is not None:
            self.send_header(adb_resident._ACT_PUBLISH_HEADER, self.act_publish)
        self.end_headers()

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[
            0
        ]  # BE-0332 Unit 4 stamps GET /source with a `?since=` mark
        if route == "/clock":
            self._respond(self.clock.encode("utf-8"), "text/plain; charset=utf-8", with_mark=False)
            return
        if route != "/source":
            self.send_error(404)
            return
        type(self).last_source_path = self.path
        self._respond(self.body.encode("utf-8"), "application/xml; charset=utf-8", with_mark=True)

    def _respond(self, payload: bytes, content_type: str, *, with_mark: bool) -> None:
        self.send_response(self.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if with_mark and self.mark is not None:
            self.send_header(adb_resident._READ_MARK_HEADER, self.mark)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:
        pass  # keep the test output quiet


def _serve_once(status: int = 200, mark: str | None = None) -> tuple[int, http.server.HTTPServer]:
    _SourceHandler.status = status
    _SourceHandler.mark = mark
    _SourceHandler.act_publish = (
        None  # opt in per test; a leaked value would confirm a publish here
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), _SourceHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port, server


def test_fetch_source_reads_the_hierarchy_over_http() -> None:
    port, server = _serve_once()
    try:
        assert adb_resident.fetch_source(port).text == _MULTI_WINDOW
    finally:
        server.shutdown()


def test_fetch_source_carries_the_read_mark_header() -> None:
    # BE-0332 Unit 3: the X-Bajutsu-Read-Mark header — the device-clock time of the newest a11y event
    # the reader had seen — rides with the dump so the driver can require a read to postdate a gesture.
    port, server = _serve_once(mark="98765")
    try:
        assert adb_resident.fetch_source(port).mark == 98765.0
    finally:
        server.shutdown()


def test_fetch_source_requests_a_since_mark_when_given() -> None:
    # BE-0332 Unit 4: the resident server blocks until a read postdates the requested mark, so the host
    # stamps the mark it took before the gesture onto the request as `?since=`. The device waits past it
    # rather than re-dumping until two hierarchies match.
    port, server = _serve_once()
    try:
        adb_resident.fetch_source(port, since=98765.0)
        assert _SourceHandler.last_source_path == "/source?since=98765.0"
    finally:
        server.shutdown()


def test_fetch_source_omits_since_when_not_requested() -> None:
    # A read with no gesture pending asks for no mark, so the path stays the bare /source that a read
    # with nothing to postdate — and an older server — expects.
    port, server = _serve_once()
    try:
        adb_resident.fetch_source(port)
        assert _SourceHandler.last_source_path == "/source"
    finally:
        server.shutdown()


def test_fetch_source_without_the_mark_header_yields_a_none_mark() -> None:
    # An older server that does not stamp the header leaves the mark None, and the driver's barrier
    # falls back to its wall-clock budget rather than failing the read.
    port, server = _serve_once(mark=None)
    try:
        assert adb_resident.fetch_source(port).mark is None
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


def test_fetch_clock_reads_the_device_clock() -> None:
    # BE-0332 Unit 3: GET /clock returns the device's current clock as a plain number, which the driver
    # takes as the mark a later read must postdate.
    port, server = _serve_once()
    try:
        assert adb_resident.fetch_clock(port) == 12345.0
    finally:
        server.shutdown()


def test_fetch_clock_returns_none_on_fault_rather_than_raising() -> None:
    # The clock is an optimisation over the wall-clock budget, so an unreachable channel degrades to
    # None (barrier falls back to the budget) rather than raising and failing a gesture.
    port, server = _serve_once()
    server.shutdown()  # nothing is listening now
    assert adb_resident.fetch_clock(port) is None


def test_fetch_clock_returns_none_on_non_200() -> None:
    # A server that answers /clock with an error (e.g. an older build without the endpoint → 404)
    # yields None, not an exception: the barrier simply keeps its wall-clock budget.
    port, server = _serve_once(status=500)
    try:
        assert adb_resident.fetch_clock(port) is None
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
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = srv.start()
    # Any earlier pair removed first — an older install can differ in signing key, which fails
    # `install -r`, or in which endpoints it serves, which is the confusing half of this channel's
    # failure modes: a `/act` that 404s against a server built with one. Then both APKs installed,
    # the blocking instrumentation spawned, a host port forwarded.
    assert calls[0] == adb.uninstall_cmd("U", adb.RESIDENT_TEST_PACKAGE)
    assert calls[1] == adb.uninstall_cmd("U", adb.RESIDENT_SERVER_PACKAGE)
    assert calls[2] == adb.install_cmd("U", str(server_apk))
    assert calls[3] == adb.install_cmd("U", str(test_apk))
    assert calls[4] == adb.forward_cmd("U")
    # The returned fetch reads over the channel and narrows to the active window (no SystemUI window).
    assert parse_hierarchy(channel.fetch(None).text) == parse_hierarchy(_APP_ONLY)


def test_start_returned_fetch_carries_the_pre_narrow_body_when_narrowing_changed_it(
    tmp_path: Path,
) -> None:
    # `RawSourceProvider`/`rawTree`: narrowing is the one real structural transform in the whole
    # raw-dump-to-Element pipeline, so a raw-tree diagnostic needs the body from *before* it, not just
    # the narrowed `text` every other caller already gets.
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "41000\n" if "forward" in args and "--remove" not in args else "",
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = srv.start()
    read = channel.fetch(None)
    assert read.raw == _MULTI_WINDOW  # the untouched, pre-narrow body
    assert read.raw != read.text  # narrowing genuinely changed something


def test_start_returned_fetch_carries_no_raw_body_when_narrowing_is_a_no_op(
    tmp_path: Path,
) -> None:
    # An active-window-only dump (no SystemUI window to strip) passes through narrow_to_active_window
    # unchanged — carrying an identical `raw` alongside `text` here would just double-write it.
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=lambda args: "41000\n" if "forward" in args and "--remove" not in args else "",
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = srv.start()
    assert channel.fetch(None).raw is None


def test_fetch_fault_stops_the_server_before_it_propagates(tmp_path: Path) -> None:
    # A read fault must tear the resident server down before the driver degrades to `uiautomator
    # dump`: a wedged-but-alive instrumentation still holds the device's single UiAutomation session,
    # so a fallback dump would read an empty tree for the rest of the lease. Releasing it here makes
    # the fallback a clean degrade.
    teardown: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "--remove" in args or "force-stop" in args:
            teardown.append(args)
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    # The readiness probe sees the channel up; the driver's first real read finds it wedged.
    reads = iter([_MULTI_WINDOW])

    def fetch(port: int, _since: float | None) -> HierarchyRead:
        try:
            return HierarchyRead(next(reads))
        except StopIteration:
            raise AdbResidentError("timed out") from None

    proc = _FakeProc()
    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: proc,
        fetch=fetch,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = srv.start()
    with pytest.raises(AdbResidentError, match="timed out"):
        channel.fetch(None)
    # The fault stopped the server: the forward was removed and the device-side package force-stopped,
    # releasing the UiAutomation session for the driver's dump fallback.
    assert proc.terminated
    assert adb.forward_remove_cmd("U", 41000) in teardown
    assert adb.force_stop_cmd("U", adb.RESIDENT_SERVER_PACKAGE) in teardown


def test_start_returns_a_channel_whose_clock_probe_reads_the_device(tmp_path: Path) -> None:
    # BE-0332 Unit 3: start() hands back both a hierarchy fetch and a device-clock probe, each closed
    # over the lease's forwarded port, so the driver can take a mark before a gesture with no arguments.
    def run(args: list[str]) -> str:
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    server_apk, test_apk = _apks(tmp_path)
    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW, mark=float(port)),
        clock=lambda port: float(port) + 1,
        server_apk=server_apk,
        test_apk=test_apk,
    )
    channel = srv.start()
    # Both callables bind the same forwarded port (41000), so they answer about this lease's channel.
    assert channel.fetch(None).mark == 41000.0
    assert channel.clock() == 41001.0


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
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
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
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
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

    def fetch(port: int, _since: float | None) -> HierarchyRead:
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
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
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

    def fetch(port: int, _since: float | None) -> HierarchyRead:
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
        fetch=lambda port, _since: HierarchyRead(_MULTI_WINDOW),
        server_apk=server_apk,
        test_apk=test_apk,
    )
    srv.start()
    srv.stop()
    assert proc.terminated
    assert proc.killed


def test_server_apks_built_needs_both_apks(tmp_path: Path) -> None:
    # The default-on gate (BE-0245 PR-D) reads via the resident channel only when the server is
    # built, so `server_apks_built` must be true only when BOTH `make -C BajutsuAndroidUIAutomatorServer build`
    # outputs are present — a half-built tree (one APK) still needs the dump path.
    server_apk, test_apk = _apks(tmp_path)
    assert adb_resident.server_apks_built(server_apk, test_apk)
    assert not adb_resident.server_apks_built(server_apk, tmp_path / "missing.apk")
    assert not adb_resident.server_apks_built(tmp_path / "missing.apk", test_apk)
    assert not adb_resident.server_apks_built(tmp_path / "a.apk", tmp_path / "b.apk")
    # The signature's params are named, so asking by keyword must answer about those same paths —
    # conftest's `_fresh_clone_resident_gate` pins only the gate's argument-less, ambient call.
    assert adb_resident.server_apks_built(server_apk=server_apk, test_apk=test_apk)


# --- POST /act: the device resolves and injects, so no coordinate crosses the channel ---


def _act_request(**over: object) -> ActRequest:
    fields: dict[str, object] = {
        "kind": "tap",
        "identity": ("stable.submit", "sent", "送信", "android.widget.Button"),
        "index": 0,
        "count": 1,
        "since": None,
        "duration_ms": None,
    }
    fields.update(over)
    return ActRequest(**fields)  # type: ignore[arg-type]


def test_act_sends_the_element_identity_and_no_coordinate() -> None:
    # The whole point of the endpoint: the device is told *which* element, never *where*. A coordinate
    # on the wire would be one the host computed a round trip earlier — the staleness this closes.
    port, server = _serve_once()
    _SourceHandler.act_status = 200
    try:
        assert adb_resident.act(port, _act_request(since=42.0, duration_ms=700)).acted is True
    finally:
        server.shutdown()
    sent = urllib.parse.parse_qs(urllib.parse.urlparse(_SourceHandler.last_act_path or "").query)
    assert sent["rid"] == ["stable.submit"] and sent["text"] == ["送信"]
    assert sent["index"] == ["0"] and sent["count"] == ["1"]
    assert sent["since"] == ["42.0"] and sent["durationMs"] == ["700"]
    assert not {"x", "y", "bounds"} & sent.keys()


def test_act_reports_a_stale_target_rather_than_raising() -> None:
    # 409 is the device saying "that identity no longer names the same nodes here". It is an ordinary
    # outcome the driver answers by re-resolving, not a channel fault, so it must not raise.
    port, server = _serve_once()
    _SourceHandler.act_status = 409
    try:
        assert adb_resident.act(port, _act_request()).acted is False
    finally:
        server.shutdown()


def test_act_reports_the_publish_the_device_confirmed() -> None:
    # The header is the device answering "this gesture has already reached the accessibility tree"
    # (BE-0339 Unit 5) — the one answer that lets the driver skip the read-lag barrier for it, which
    # it may do only on a mark the device actually observed, never on an assumption of its own.
    port, server = _serve_once()
    _SourceHandler.act_status = 200
    _SourceHandler.act_publish = "98765"
    try:
        assert adb_resident.act(port, _act_request()) == ActOutcome(
            acted=True, published_mark=98765.0
        )
    finally:
        server.shutdown()


@pytest.mark.parametrize("header", [None, "not-a-mark"])
def test_act_reports_no_publish_when_the_device_did_not_confirm_one(header: str | None) -> None:
    # Two servers answer the same way here, deliberately: one that waited its budget and saw no event,
    # and an older one that never waited at all and so sends no header. Both mean "unconfirmed", which
    # leaves the barrier armed exactly as it stood — so neither needs the driver to know which it was.
    # A malformed value degrades the same way rather than failing a gesture that actually landed.
    port, server = _serve_once()
    _SourceHandler.act_status = 200
    _SourceHandler.act_publish = header
    try:
        assert adb_resident.act(port, _act_request()) == ActOutcome(acted=True, published_mark=None)
    finally:
        server.shutdown()


def test_act_raises_on_a_server_without_the_endpoint() -> None:
    # An older resident server serves reads but 404s this path. That has to surface as a channel error
    # so the driver degrades to its coordinate actuators rather than silently skipping the gesture.
    port, server = _serve_once()
    _SourceHandler.act_status = 404
    try:
        with pytest.raises(AdbResidentError, match="404"):
            adb_resident.act(port, _act_request())
    finally:
        server.shutdown()


def test_act_raises_when_the_platform_rejected_the_injection() -> None:
    # The Kotlin endpoint answers a non-{200,404,409} status when the injector itself
    # (UiDevice.click / .swipe / injectInputEvent) reports the touch never reached the screen — never
    # a silent 200. That must surface as a channel fault so `_device_act` degrades to the coordinate
    # path for this one gesture, exactly as any other resident-side fault does; nothing was injected,
    # so the coordinate path is not a second touch.
    port, server = _serve_once()
    _SourceHandler.act_status = 500
    try:
        with pytest.raises(AdbResidentError, match="500"):
            adb_resident.act(port, _act_request())
    finally:
        server.shutdown()


def test_act_separates_a_reply_lost_after_the_send_from_one_never_sent() -> None:
    # The two socket faults mean opposite things, because the device injects before it answers. A
    # connect that never happened injected nothing and is safe to retry on coordinates; a reply lost
    # after the POST went out may sit on top of a gesture that already landed.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[
            1
        ]  # bound only long enough to reserve a port nothing listens on
    with pytest.raises(AdbResidentError) as never_sent:
        adb_resident.act(closed, _act_request(), timeout=0.2)
    assert not isinstance(never_sent.value, AdbActUncertain)

    port, server = _serve_once()
    _SourceHandler.act_drop_reply = True
    try:
        with pytest.raises(AdbActUncertain, match="sent but its reply was lost"):
            adb_resident.act(port, _act_request())
    finally:
        _SourceHandler.act_drop_reply = False
        server.shutdown()
