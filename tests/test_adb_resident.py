"""Tests for the resident UI Automator server channel (BE-0245).

The resident channel reaches an on-device server over `adb forward` + HTTP instead of paying
`uiautomator dump`'s per-invocation startup. These cover the three deterministic, device-free
pieces: narrowing the server's whole-screen dump to the active window so `parse_hierarchy` yields the
same Elements as the dump path, the HTTP client (against a real loopback server, no mock), and the
server lifecycle over an injected `run`. Real on-device verification is a later slice (PR-D).
"""

from __future__ import annotations

import base64
import http.client
import http.server
import socket
import subprocess
import threading
import urllib.parse
from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb, adb_resident
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import (
    ActOutcome,
    ActRequest,
    AdbActUncertain,
    AdbActUnsupported,
    AdbResidentError,
    HierarchyRead,
    elements_with_identities,
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


def _narrowed_elements(xml: str) -> list[base.Element]:
    return elements_with_identities(adb_resident.narrowed_root(xml)[0])[0]


def test_narrow_matches_the_active_window_dump() -> None:
    # The whole point of PR-C's equivalence work: after narrowing, the resident dump parses to exactly
    # the Elements the `uiautomator dump` path produces — the SystemUI window is gone.
    assert _narrowed_elements(_MULTI_WINDOW) == parse_hierarchy(_APP_ONLY)
    # Guard the test itself: the two dumps genuinely differ before narrowing.
    assert parse_hierarchy(_MULTI_WINDOW) != parse_hierarchy(_APP_ONLY)
    assert adb_resident.narrowed_root(_MULTI_WINDOW)[1] is True  # a window really was dropped


def test_narrow_leaves_the_active_window_dump_untouched() -> None:
    # A dump with no system window (already the active window, e.g. the fallback path fed back through)
    # passes through so the two transports converge on identical Elements.
    assert _narrowed_elements(_APP_ONLY) == parse_hierarchy(_APP_ONLY)
    assert adb_resident.narrowed_root(_APP_ONLY)[1] is False  # nothing to strip


def test_narrow_answers_no_tree_for_unparseable_input() -> None:
    # Garbage/mid-transition text yields no tree, and the driver's empty-tree degrade applies as
    # before — narrowing never masks a bad read.
    assert adb_resident.narrowed_root("null root node") == (None, False)


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
    els = _narrowed_elements(multi)
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

    type_status = 200  # what POST /type answers
    type_reason = b""  # the body of that answer
    type_drop_reply = False  # accept the request, then hang up without answering
    last_type_path: str | None = None  # the full POST /type target the client last sent
    last_type_body: bytes | None = None  # the body of that request

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] == "/type":
            self._do_type()
            return
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

    def _do_type(self) -> None:
        type(self).last_type_path = self.path
        type(self).last_type_body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.type_drop_reply:
            self.close_connection = True
            self.wfile.close()
            return
        self.send_response(self.type_status)
        self.send_header("Content-Length", str(len(self.type_reason)))
        self.end_headers()
        self.wfile.write(self.type_reason)

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


class _ActTreeHandler(_SourceHandler):
    """A `/act` that answers the caught-up tree a confirmed gesture carries (BE-0407 unit 19)."""

    act_body: bytes = _MULTI_WINDOW.encode("utf-8")
    act_mark: str | None = "98765"

    def do_POST(self) -> None:
        type(self).last_act_path = self.path
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(self.act_body)))
        self.send_header(adb_resident._ACT_PUBLISH_HEADER, "98765")
        if self.act_mark is not None:
            self.send_header(adb_resident._READ_MARK_HEADER, self.act_mark)
        self.end_headers()
        self.wfile.write(self.act_body)


def _serve_act_tree(
    body: bytes = _MULTI_WINDOW.encode("utf-8"), mark: str | None = "98765"
) -> tuple[int, http.server.HTTPServer]:
    _ActTreeHandler.act_body = body
    _ActTreeHandler.act_mark = mark
    server = http.server.HTTPServer(("127.0.0.1", 0), _ActTreeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port, server


def _tap(port: int, *, keepalive: adb_resident.Keepalive | None = None) -> ActOutcome:
    return adb_resident.act(
        port,
        ActRequest(
            kind="tap", identity=("a", "", "", "C"), index=0, count=1, since=None, duration_ms=None
        ),
        keepalive=keepalive,
    )


def test_a_confirmed_gestures_reply_carries_the_narrowed_tree() -> None:
    # BE-0407 unit 19: the tree the device dumped after it confirmed the publish, narrowed here the
    # same way a `/source` read is, so the driver can adopt it in place of the read it would have taken.
    port, server = _serve_act_tree()
    try:
        outcome = _tap(port)
        assert outcome.read is not None
        assert outcome.read.mark == 98765.0
        assert outcome.read.text == _MULTI_WINDOW  # the device's own reply, untouched
        assert outcome.read.narrowed is True
        assert elements_with_identities(outcome.read.root)[0] == parse_hierarchy(_APP_ONLY)
    finally:
        server.shutdown()


def test_a_reply_without_a_read_mark_carries_no_tree() -> None:
    # The mark is what separates the two bodies this endpoint answers — a bare `ok` and a post-publish
    # dump — and it is also what the driver checks before trusting the tree. An older server sends
    # neither, so absence needs no version negotiation.
    port, server = _serve_act_tree(body=b"ok\n", mark=None)
    try:
        assert _tap(port).read is None
    finally:
        server.shutdown()


def test_a_tree_the_reply_could_not_carry_intact_is_dropped_not_raised() -> None:
    # The gesture itself landed. Losing this optimisation costs the read it would have saved and
    # nothing else, so an undecodable or unparseable body degrades to "read again" rather than failing
    # an actuation that actually happened.
    for body in (b"\xff\xfe not utf-8", b"<hierarchy>truncated"):
        port, server = _serve_act_tree(body=body)
        try:
            outcome = _tap(port)
            assert outcome.acted is True
            assert outcome.read is None
        finally:
            server.shutdown()


def test_an_unreadable_socket_counts_as_stale_rather_than_reused() -> None:
    # `_is_stale` answers on any doubt, including a socket that cannot even be polled: reconnecting
    # costs a handshake, while writing onto an unknown socket risks a half-sent gesture.
    conn = http.client.HTTPConnection("127.0.0.1", 1)
    conn.sock = socket.socket()
    conn.sock.close()  # a closed socket raises from `select`, the branch under test
    assert adb_resident._is_stale(conn) is True


def test_a_device_that_cannot_be_asked_about_its_packages_reinstalls(tmp_path: Path) -> None:
    # `_packages_installed` answers False on any fault, so an unanswerable device puts the APKs back
    # rather than starting a server that may not be there (BE-0407 unit 22).
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        if "path" in args:
            raise subprocess.CalledProcessError(1, args)
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    server_apk, test_apk = _apks(tmp_path)
    adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
        server_apk=server_apk,
        test_apk=test_apk,
        installed=installed,
    ).start()
    assert len(_installs(calls)) == 4


def test_fetch_source_asks_for_native_z_only_when_the_target_opted_in() -> None:
    # BE-0407 unit 18: the walk answering `nativeZ` covers every node on every read and returns nothing
    # at all for an app that opted no view in, so the device does it only when asked — and the ask
    # comes from the target's own config, never from the driver guessing.
    port, server = _serve_once()
    try:
        adb_resident.fetch_source(port)
        assert _SourceHandler.last_source_path == "/source"
        adb_resident.fetch_source(port, native_z=True)
        assert _SourceHandler.last_source_path == "/source?nativeZ=1"
        adb_resident.fetch_source(port, since=98765.0, native_z=True)
        assert _SourceHandler.last_source_path == "/source?since=98765.0&nativeZ=1"
    finally:
        server.shutdown()


class _KeepaliveHandler(_SourceHandler):
    """The stub speaking HTTP/1.1 with the connection kept open, as the resident server now does.

    `BaseHTTPRequestHandler` defaults to HTTP/1.0 and hangs up after each reply, which is exactly what
    `_is_stale` is built to notice — so a keep-alive test against the default stub would only ever
    prove the reconnect path.
    """

    protocol_version = "HTTP/1.1"


def _serve_keepalive() -> tuple[int, http.server.HTTPServer]:
    _SourceHandler.status = 200
    _SourceHandler.mark = None
    _SourceHandler.act_publish = None
    server = http.server.HTTPServer(("127.0.0.1", 0), _KeepaliveHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_port, server


def test_a_kept_connection_carries_the_next_call_instead_of_reconnecting() -> None:
    # BE-0407 unit 21: every read, clock probe and gesture used to pay its own TCP handshake — 30-150ms
    # against a channel a scenario touches several times per step. Pinned on the socket itself rather
    # than on a timing: the same connection object serving the second call is what "no handshake" means.
    port, server = _serve_keepalive()
    keepalive = adb_resident.Keepalive()
    try:
        adb_resident.fetch_source(port, keepalive=keepalive)
        first = keepalive.conn
        assert first is not None
        keepalive.last_success_at = (
            None  # the idle ceiling has its own test; this one is reuse only
        )
        adb_resident.fetch_clock(port, keepalive=keepalive)
        assert keepalive.conn is first  # the clock probe rode the read's connection
    finally:
        # Before `shutdown()`: the server thread is parked inside this connection's own request loop
        # until the client hangs up, exactly as the device server is.
        keepalive.discard()
        server.shutdown()


def test_a_connection_left_idle_too_long_is_reconnected_before_it_is_used() -> None:
    # The peek narrows the race against the server's own 5s idle close but cannot close it, so a
    # connection old enough for the peek to matter is retired outright. Without this ceiling, a
    # gesture could be written onto a socket the server had already torn down — the ambiguous
    # half-sent request the whole staleness discipline exists to avoid.
    port, server = _serve_keepalive()
    keepalive = adb_resident.Keepalive()
    try:
        adb_resident.fetch_source(port, keepalive=keepalive)
        first = keepalive.conn
        keepalive.last_success_at -= adb_resident._KEEPALIVE_IDLE_RECONNECT_S + 1  # type: ignore[operator]
        adb_resident.fetch_source(port, keepalive=keepalive)
        assert keepalive.conn is not first
    finally:
        keepalive.discard()
        server.shutdown()


def test_a_reused_connection_takes_this_calls_own_timeout() -> None:
    # A read must not inherit an actuation's longer window, and — the damaging direction — an
    # actuation must not inherit a read's shorter one, where a slow press-and-hold would time out and
    # reach the driver as "may or may not have landed" on a gesture that was still in flight.
    port, server = _serve_keepalive()
    keepalive = adb_resident.Keepalive()
    try:
        adb_resident.fetch_source(port, timeout=5.0, keepalive=keepalive)
        assert keepalive.conn is not None and keepalive.conn.sock is not None
        assert keepalive.conn.sock.gettimeout() == 5.0
        _tap(port, keepalive=keepalive)
        assert keepalive.conn is not None and keepalive.conn.sock is not None
        assert keepalive.conn.sock.gettimeout() == 10.0  # act's own, longer window
    finally:
        keepalive.discard()
        server.shutdown()


def test_a_gesture_the_channel_could_not_even_reach_degrades_rather_than_raising_a_socket_error() -> (
    None
):
    # Connecting eagerly moved the connect out of `act`'s own `try`, and a raw `OSError` then escaped
    # `_device_act`'s three-exception catch and failed the step — on the one fault class that is
    # provably safe to fall back from, since nothing had been sent. The conversion belongs to the
    # channel, so all three callers are covered by construction.
    keepalive = adb_resident.Keepalive()
    with socket.socket() as probe:  # a port nothing is listening on
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    with pytest.raises(AdbResidentError):
        _tap(dead, keepalive=keepalive)
    with pytest.raises(AdbResidentError):
        adb_resident.fetch_source(dead, keepalive=keepalive)
    assert adb_resident.fetch_clock(dead, keepalive=keepalive) is None


def test_a_stale_reply_keeps_the_connection_while_a_lost_one_retires_it() -> None:
    # A `409` is a complete exchange and leaves the socket good; a reply that never arrived leaves it
    # in an unknown state, and reusing it would let the next call parse whatever is left as its own
    # answer — for a gesture, a previous exchange's confirmation.
    port, server = _serve_keepalive()
    keepalive = adb_resident.Keepalive()
    try:
        _SourceHandler.act_status = 409
        _tap(port, keepalive=keepalive)
        assert keepalive.conn is not None  # the exchange completed; the connection is still good
        _SourceHandler.act_drop_reply = True
        with pytest.raises(AdbActUncertain):
            _tap(port, keepalive=keepalive)
        assert keepalive.conn is None
    finally:
        _SourceHandler.act_status = 200
        _SourceHandler.act_drop_reply = False
        keepalive.discard()
        server.shutdown()


def test_a_peer_that_hung_up_is_reconnected_rather_than_written_to() -> None:
    # The server's own idle ceiling can close a kept connection with nothing on this side to notice.
    # A zero-timeout `select` answers that *before* any byte of the next request is sent, so the ordinary
    # idle close reconnects cleanly instead of surfacing as a half-sent request whose delivery is
    # ambiguous — the distinction `act` cannot guess through, since a re-sent gesture is a second touch.
    port, server = _serve_once()  # HTTP/1.0: hangs up after every reply, like an idle-closing peer
    keepalive = adb_resident.Keepalive()
    try:
        adb_resident.fetch_source(port, keepalive=keepalive)
        first = keepalive.conn
        assert adb_resident.fetch_source(port, keepalive=keepalive).text == _MULTI_WINDOW
        assert (
            keepalive.conn is not first
        )  # noticed and reconnected, not written onto a dead socket
    finally:
        keepalive.discard()
        server.shutdown()


def test_a_call_that_faults_never_leaves_its_connection_to_be_reused() -> None:
    # A connection a failure touched is in an unknown state — a half-sent request, an unread reply —
    # and reusing it would desynchronize the next call onto the previous one's bytes.
    port, server = _serve_once(status=500)
    keepalive = adb_resident.Keepalive()
    try:
        with pytest.raises(AdbResidentError):
            adb_resident.fetch_source(port, keepalive=keepalive)
        assert keepalive.conn is None
    finally:
        keepalive.discard()
        server.shutdown()


def test_a_one_shot_caller_still_gets_a_connection_of_its_own() -> None:
    # Omitting the keepalive is the behavior every caller had before this unit, and is what a probe or
    # a test that must not share a socket relies on.
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
    """The two APK paths, created once. Existing content is left alone so a test can rebuild one."""
    server_apk = tmp_path / "server-debug.apk"
    test_apk = tmp_path / "server-debug-androidTest.apk"
    for apk in (server_apk, test_apk):
        if not apk.exists():
            apk.write_bytes(b"apk")
    return server_apk, test_apk


def _install_server(
    tmp_path: Path,
    calls: list[list[str]],
    installed: dict[str, tuple[str, str]] | None,
    *,
    present: bool = True,
) -> adb_resident.ResidentServer:
    """A `ResidentServer` whose every adb call is recorded, sharing one `installed` record."""
    server_apk, test_apk = _apks(tmp_path)

    def run(args: list[str]) -> str:
        calls.append(args)
        if "forward" in args and "--remove" not in args:
            return "41000\n"
        if "path" in args:
            return "package:/data/app/server.apk\n" if present else "\n"
        return ""

    return adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
        server_apk=server_apk,
        test_apk=test_apk,
        installed=installed,
    )


def _installs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if "install" in c or "uninstall" in c]


def test_a_second_lease_reuses_the_apk_pair_this_run_already_installed(tmp_path: Path) -> None:
    # BE-0407 unit 22: an uninstall of both packages plus two `install -r` calls is the dominant cost
    # of starting a lease, and every lease after the first put back the very same bytes. The digests
    # pin the signing key too — identical bytes cannot be signed differently — which is exactly what
    # the uninstall-first rule was guarding against.
    installed: dict[str, tuple[str, str]] = {}
    first: list[list[str]] = []
    _install_server(tmp_path, first, installed).start()
    assert len(_installs(first)) == 4  # two uninstalls, two installs
    second: list[list[str]] = []
    _install_server(tmp_path, second, installed).start()
    assert _installs(second) == []


def test_a_rebuilt_apk_is_installed_again(tmp_path: Path) -> None:
    # The digests are the whole safety argument for the skip — identical bytes cannot be signed
    # differently — so a record keyed on anything weaker (the paths, say) would let a rebuilt server
    # be skipped and leave the device running the previous build for the rest of the run.
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    (tmp_path / "server-debug.apk").write_bytes(b"a different build")
    calls: list[list[str]] = []
    _install_server(tmp_path, calls, installed).start()
    assert len(_installs(calls)) == 4


def test_the_record_is_kept_per_device_not_per_run(tmp_path: Path) -> None:
    # A record that ignored the serial would skip the install on a device that never received the
    # pair, and the lease would then fail on a `/act` the server there does not serve — the confusing
    # half of this channel's failure modes.
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    calls: list[list[str]] = []
    other = _install_server(tmp_path, calls, installed)
    other._serial = "OTHERDEVICE"  # the same run, a second device in the lane
    other.start()
    assert len(_installs(calls)) == 4


def test_one_missing_package_is_enough_to_reinstall(tmp_path: Path) -> None:
    # Both packages are checked, not just the first: the instrumentation APK alone going missing
    # leaves a server that answers nothing, which is exactly what the check exists to catch.
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    calls: list[list[str]] = []
    server_apk, test_apk = _apks(tmp_path)

    def run(args: list[str]) -> str:
        calls.append(args)
        if "path" in args:
            return "" if adb.RESIDENT_TEST_PACKAGE in args else "package:/data/app/server.apk\n"
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
        server_apk=server_apk,
        test_apk=test_apk,
        installed=installed,
    ).start()
    assert len(_installs(calls)) == 4


def test_a_pm_path_answer_that_is_not_a_package_line_reinstalls(tmp_path: Path) -> None:
    # "Non-empty output" is not "installed": an adb error line or a `pm` diagnostic would otherwise
    # read as a present package and skip an install the device needs.
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    calls: list[list[str]] = []
    server_apk, test_apk = _apks(tmp_path)

    def run(args: list[str]) -> str:
        calls.append(args)
        if "path" in args:
            return "error: device offline\n"
        return "41000\n" if "forward" in args and "--remove" not in args else ""

    adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
        server_apk=server_apk,
        test_apk=test_apk,
        installed=installed,
    ).start()
    assert len(_installs(calls)) == 4


def test_a_package_removed_out_of_band_is_installed_again(tmp_path: Path) -> None:
    # The record says "this run installed that pair here"; it cannot say the device still carries it.
    # `pm path` is checked rather than assumed, so a package gone from under the run reinstalls
    # instead of leaving the lease to fail on a server that is not there.
    installed: dict[str, tuple[str, str]] = {}
    _install_server(tmp_path, [], installed).start()
    calls: list[list[str]] = []
    _install_server(tmp_path, calls, installed, present=False).start()
    assert len(_installs(calls)) == 4


def test_a_caller_that_keeps_no_record_reinstalls_every_lease(tmp_path: Path) -> None:
    # The default (`installed=None`) is the behavior every caller had before this unit: no record, so
    # nothing to reuse. A skip must be something a caller opts into by owning the record.
    first: list[list[str]] = []
    _install_server(tmp_path, first, None).start()
    second: list[list[str]] = []
    _install_server(tmp_path, second, None).start()
    assert len(_installs(first)) == len(_installs(second)) == 4


def test_a_failed_install_leaves_no_record_claiming_the_pair_is_there(tmp_path: Path) -> None:
    # A record written before the installs finish would let the next lease skip an install that never
    # landed. It is dropped up front and only rewritten once both installs return.
    installed: dict[str, tuple[str, str]] = {"U": ("stale", "stale")}
    server_apk, test_apk = _apks(tmp_path)

    def run(args: list[str]) -> str:
        if "install" in args and "uninstall" not in args:
            raise subprocess.CalledProcessError(1, args)
        return "package:/data/app/server.apk\n" if "path" in args else ""

    srv = adb_resident.ResidentServer(
        "U",
        run=run,
        spawn=lambda argv: _FakeProc(),
        server_apk=server_apk,
        test_apk=test_apk,
        installed=installed,
    )
    with pytest.raises(AdbResidentError):
        srv.start()
    assert installed == {}


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
    # The returned fetch reads over the channel and hands back the narrowed tree — the active
    # window alone, no SystemUI window — already parsed, so the driver never parses the body twice.
    assert elements_with_identities(channel.fetch(None).root)[0] == parse_hierarchy(_APP_ONLY)


def test_start_returned_fetch_carries_the_pre_narrow_body_when_narrowing_changed_it(
    tmp_path: Path,
) -> None:
    # `RawSourceProvider`/`rawTree`: narrowing is the one real structural transform in the whole
    # raw-dump-to-Element pipeline, so a raw-tree diagnostic needs the body from *before* it, not just
    # the tree every other caller consumes. `text` is that body, and `narrowed` is what says the two
    # differ (BE-0407 unit 23 moved the narrowed half from a string to the parsed tree).
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
    assert read.text == _MULTI_WINDOW  # the untouched, pre-narrow body
    assert read.narrowed is True  # narrowing genuinely changed something
    assert elements_with_identities(read.root)[0] == parse_hierarchy(_APP_ONLY)


def test_start_returned_fetch_carries_no_raw_body_when_narrowing_is_a_no_op(
    tmp_path: Path,
) -> None:
    # An active-window-only dump has no SystemUI window to strip, so narrowing changed nothing —
    # and a `rawTree` capture told so writes one body rather than two identical ones.
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
    assert channel.fetch(None).narrowed is False


def test_a_native_z_target_tells_the_device_not_to_build_a_tree_it_cannot_use(
    tmp_path: Path,
) -> None:
    # BE-0407 unit 19's saved round trip is given up on a target that asked for `nativeZ` (unit 18):
    # `/act`'s reply cannot carry that reading, so a tree seeded from it would report every element's
    # position as absent — indistinguishable from an app that opted no view in. Said on the request,
    # not by discarding the reply, or the device would spend a settled dump per gesture on a body the
    # host throws away — a regression on exactly the targets unit 18 exists for.
    port, server = _serve_once()
    try:
        adb_resident.act(port, _act_request(), want_tree=False)
        assert "tree=0" in (_SourceHandler.last_act_path or "")
        adb_resident.act(port, _act_request())
        assert "tree=1" in (_SourceHandler.last_act_path or "")
    finally:
        server.shutdown()


def test_the_targets_native_z_choice_binds_to_the_leases_act_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The lease binds the answer once, so a target that opted in never has to be asked again per
    # gesture — and a target that did not keeps unit 19's saved round trip.
    server_apk, test_apk = _apks(tmp_path)
    asked: list[bool] = []
    monkeypatch.setattr(
        # `ResidentServer` reads `act` through its own module's import, so the package attribute is
        # not the binding this test replaces (BE-0411).
        adb_resident.resident_server,
        "act",
        lambda port, request, **kw: (
            asked.append(bool(kw["want_tree"]))  # type: ignore[func-returns-value]
            or ActOutcome(acted=True, published_mark=None)
        ),
    )
    for native_z in (True, False):
        adb_resident.ResidentServer(
            "U",
            run=lambda args: "41000\n" if "forward" in args and "--remove" not in args else "",
            spawn=lambda argv: _FakeProc(),
            fetch=lambda port, _since: HierarchyRead(_APP_ONLY),
            server_apk=server_apk,
            test_apk=test_apk,
            native_z=native_z,
        ).start().act(_act_request())
    assert asked == [False, True]  # opted in asks for no tree; the plain target still wants one


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


def test_type_text_carries_the_text_in_the_body_never_the_target() -> None:
    # A typed value may be a secret, so it stays out of the request target a proxy or a log would keep.
    port, server = _serve_once()
    _SourceHandler.type_status = 200
    try:
        adb_resident.type_text(port, "馬術 🐎")
    finally:
        server.shutdown()
    assert _SourceHandler.last_type_path == "/type"
    assert base64.b64decode(_SourceHandler.last_type_body or b"").decode("utf-8") == "馬術 🐎"


def test_type_text_sends_the_mark_the_devices_read_must_postdate() -> None:
    port, server = _serve_once()
    try:
        adb_resident.type_text(port, "馬", 42.0)
    finally:
        server.shutdown()
    assert _SourceHandler.last_type_path == "/type?since=42.0"


def test_type_text_raises_the_devices_reason_when_it_refuses() -> None:
    port, server = _serve_once()
    _SourceHandler.type_status = 422
    _SourceHandler.type_reason = b"the focused field is a password field\n"
    try:
        with pytest.raises(AdbResidentError, match="HTTP 422: the focused field is a password"):
            adb_resident.type_text(port, "馬")
    finally:
        _SourceHandler.type_status = 200
        _SourceHandler.type_reason = b""
        server.shutdown()


def test_type_text_separates_a_lost_reply_from_one_never_sent() -> None:
    # Resending after a lost reply could enter the text twice; a connect that never happened cannot.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = probe.getsockname()[1]
    with pytest.raises(AdbResidentError) as never_sent:
        adb_resident.type_text(closed, "馬", timeout=0.2)
    assert not isinstance(never_sent.value, AdbActUncertain)

    port, server = _serve_once()
    _SourceHandler.type_drop_reply = True
    try:
        with pytest.raises(AdbActUncertain, match="sent but its reply was lost"):
            adb_resident.type_text(port, "馬")
    finally:
        _SourceHandler.type_drop_reply = False
        server.shutdown()


def test_type_text_names_a_server_without_the_endpoint() -> None:
    port, server = _serve_once()
    _SourceHandler.type_status = 404
    try:
        with pytest.raises(AdbActUnsupported, match="no /type endpoint"):
            adb_resident.type_text(port, "馬")
    finally:
        _SourceHandler.type_status = 200
        server.shutdown()


def test_the_lease_channel_hands_text_to_the_type_probe(tmp_path: Path) -> None:
    server_apk, test_apk = tmp_path / "s.apk", tmp_path / "t.apk"
    server_apk.write_bytes(b"s")
    test_apk.write_bytes(b"t")
    typed: list[tuple[int, str, float | None]] = []
    resident = adb_resident.ResidentServer(
        "S",
        run=lambda argv: "4321\n" if "forward" in argv else "",
        spawn=lambda argv: _FakeProc(),
        fetch=lambda port, since: HierarchyRead("<hierarchy/>"),
        type_probe=lambda port, text, since: typed.append((port, text, since)),
        server_apk=server_apk,
        test_apk=test_apk,
    )
    resident.start().type_text("馬", 7.0)
    assert typed == [(4321, "馬", 7.0)]


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
