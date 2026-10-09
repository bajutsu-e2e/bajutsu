"""Tests for the usbmuxd host → device bridge the real-device XCUITest channel rides.

A fake usbmuxd on a UNIX socket in `tmp_path` speaks the plist protocol and, on `Connect`, splices
the client to a local TCP server standing in for the device port — so the framing, device lookup,
refusal, and byte forwarding are all exercised without a device or the real `/var/run/usbmuxd`.
"""

from __future__ import annotations

import contextlib
import logging
import plistlib
import socket
import struct
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.platform_lifecycle.environments.xcuitest import _usbmux as usbmux
from bajutsu.common.platform_lifecycle.environments.xcuitest._usbmux import (
    UsbmuxError,
    UsbmuxForwarder,
    UsbmuxRefused,
    connect,
    device_id,
)

_UDID = "00008120-001669E60168C01E"
_HEADER = struct.Struct("<IIII")


def _read_msg(sock: socket.socket) -> dict[str, Any]:
    head = sock.recv(_HEADER.size, socket.MSG_WAITALL)
    length, version, kind, _ = _HEADER.unpack(head)
    assert (version, kind) == (1, 8)  # the plist dialect
    body = sock.recv(length - _HEADER.size, socket.MSG_WAITALL)
    return dict(plistlib.loads(body))


def _write_msg(sock: socket.socket, payload: dict[str, Any]) -> None:
    body = plistlib.dumps(payload)
    sock.sendall(_HEADER.pack(_HEADER.size + len(body), 1, 8, 1) + body)


class _FakeUsbmuxd:
    """A usbmuxd listing `devices`; `Connect` to `ports[port]` splices to that local TCP port."""

    def __init__(self, path: Path, devices: list[dict[str, Any]], ports: dict[int, int]) -> None:
        self.path = str(path)
        self.devices = devices
        self.ports = ports
        self.connects: list[dict[str, Any]] = []
        self._socks: list[socket.socket] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.path)
        self._server.listen(8)
        threading.Thread(target=self._loop, daemon=True).start()

    def close(self) -> None:
        self._server.close()
        for sock in self._socks:
            sock.close()

    def _loop(self) -> None:
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        # `close()` may tear the sockets down under a handler still mid-exchange; that is the test
        # ending, not a fault to report from a background thread.
        with contextlib.suppress(OSError, struct.error, plistlib.InvalidFileException):
            self._exchange(client)

    def _exchange(self, client: socket.socket) -> None:
        self._socks.append(client)
        msg = _read_msg(client)
        if msg["MessageType"] == "ListDevices":
            _write_msg(client, {"DeviceList": self.devices})
            client.close()
            return
        self.connects.append(msg)
        port = socket.ntohs(msg["PortNumber"])
        if port not in self.ports:
            _write_msg(client, {"MessageType": "Result", "Number": 3})
            client.close()
            return
        _write_msg(client, {"MessageType": "Result", "Number": 0})
        device = socket.create_connection(("127.0.0.1", self.ports[port]))
        self._socks.append(device)
        _splice(client, device)


def _splice(a: socket.socket, b: socket.socket) -> None:
    def _copy(src: socket.socket, dst: socket.socket) -> None:
        with contextlib.suppress(OSError):
            while data := src.recv(4096):
                dst.sendall(data)
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)

    threading.Thread(target=_copy, args=(a, b), daemon=True).start()
    threading.Thread(target=_copy, args=(b, a), daemon=True).start()


def _read_to_eof(sock: socket.socket) -> bytes:
    """`recv`, counting a reset as the EOF it equally signals (the peer closed with data unread)."""
    try:
        return sock.recv(4096)
    except ConnectionResetError:
        return b""


def _device(device_id: int, serial: str, connection: str) -> dict[str, Any]:
    return {
        "DeviceID": device_id,
        "Properties": {"SerialNumber": serial, "ConnectionType": connection},
    }


@pytest.fixture
def echo_port() -> Iterator[int]:
    """A TCP server that answers each line with `echo:` + the line, standing in for the runner."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(8)

    def _serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(4096)
                conn.sendall(b"echo:" + data)

    threading.Thread(target=_serve, daemon=True).start()
    yield server.getsockname()[1]
    server.close()


@pytest.fixture
def mux_path() -> Iterator[Path]:
    # AF_UNIX paths are capped near 104 bytes on macOS, which pytest's tmp_path can exceed.
    path = Path("/tmp") / f"bajutsu-usbmux-{uuid.uuid4().hex[:12]}.sock"
    yield path
    path.unlink(missing_ok=True)


def test_device_id_prefers_usb_and_matches_with_or_without_the_hyphen(mux_path: Path) -> None:
    mux = _FakeUsbmuxd(
        mux_path,
        [
            _device(7, "00008120001669E60168C01E", "Network"),
            _device(3, _UDID, "USB"),
            _device(9, "OTHER", "USB"),
        ],
        {},
    )
    try:
        assert device_id(_UDID, str(mux_path)) == 3
    finally:
        mux.close()


def test_device_id_fails_loudly_for_an_unlisted_device(mux_path: Path) -> None:
    mux = _FakeUsbmuxd(mux_path, [_device(9, "OTHER", "USB")], {})
    try:
        with pytest.raises(UsbmuxError, match="does not list device"):
            device_id(_UDID, str(mux_path))
    finally:
        mux.close()


def test_an_unreachable_usbmuxd_is_a_usbmux_error(tmp_path: Path) -> None:
    with pytest.raises(UsbmuxError, match="cannot reach usbmuxd"):
        device_id(_UDID, str(tmp_path / "absent.sock"))


def test_connect_sends_the_port_in_network_order_and_reports_a_refusal(mux_path: Path) -> None:
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    try:
        with pytest.raises(UsbmuxRefused, match="refused device port 8100"):
            connect(_UDID, 8100, str(mux_path))
        assert mux.connects[0]["DeviceID"] == 3
        assert mux.connects[0]["PortNumber"] == socket.htons(8100)
    finally:
        mux.close()


def test_the_forwarder_tunnels_a_host_connection_to_the_same_device_port(
    mux_path: Path, echo_port: int
) -> None:
    # The fake mux maps the forwarder's own port to the echo server: one number names both ends.
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        port = fwd.start()
        mux.ports[port] = echo_port
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"GET /health")
            assert s.recv(4096) == b"echo:GET /health"
        assert socket.ntohs(mux.connects[0]["PortNumber"]) == port
    finally:
        fwd.close()
        mux.close()


def test_a_refused_device_port_closes_the_host_connection(mux_path: Path) -> None:
    # The runner is not listening yet: the host side sees EOF at once, which the health poll reads
    # as not-ready rather than hanging.
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        port = fwd.start()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"GET /health")
            try:
                reply = s.recv(4096)
            except ConnectionResetError:  # closed with the request unread: a reset, not an EOF
                reply = b""
            assert reply == b""
    finally:
        fwd.close()
        mux.close()


def test_start_fails_before_binding_when_the_device_is_absent(mux_path: Path) -> None:
    mux = _FakeUsbmuxd(mux_path, [], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        with pytest.raises(UsbmuxError):
            fwd.start()
        assert fwd.port == 0
    finally:
        mux.close()


def test_close_stops_accepting_and_is_idempotent(mux_path: Path) -> None:
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        port = fwd.start()
        fwd.close()
        fwd.close()
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
    finally:
        mux.close()


def test_a_silent_usbmuxd_fails_within_the_handshake_ceiling(
    mux_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A mux that accepts but never answers must not hang the spawn or a health-probe thread.
    monkeypatch.setattr(usbmux, "_HANDSHAKE_TIMEOUT", 0.2)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(mux_path))
    server.listen(1)
    try:
        with pytest.raises(UsbmuxError, match="did not answer"):
            device_id(_UDID, str(mux_path))
    finally:
        server.close()


def test_a_joined_tunnel_carries_no_idle_deadline(mux_path: Path, echo_port: int) -> None:
    # The handshake ceiling is for the exchange with usbmuxd only; the driver keeps the joined
    # connection open between calls, so it must not time out while idle.
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {8100: echo_port})
    try:
        with connect(_UDID, 8100, str(mux_path)) as sock:
            assert sock.gettimeout() is None
    finally:
        mux.close()


def test_close_wakes_a_live_tunnel(mux_path: Path) -> None:
    # `close()` runs on the main thread while both pump threads sit in `recv()` on a live tunnel;
    # shutting the sockets down is what wakes them, so the host side must read EOF promptly.
    silent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        port = fwd.start()
        mux.ports[port] = silent.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            silent.settimeout(5)
            device_side, _ = silent.accept()  # the splice is live and idle
            deadline = time.monotonic() + 5
            while not fwd._open and time.monotonic() < deadline:
                time.sleep(0.01)
            assert fwd._open, "the tunnel never registered as open"
            fwd.close()
            assert _read_to_eof(client) == b""
            device_side.close()
    finally:
        fwd.close()
        mux.close()
        silent.close()


def test_a_vanished_device_is_reported_as_a_warning(
    mux_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A device that drops off usbmuxd mid-run is not "not ready yet": the driver only sees EOF, so
    # the reason must surface above DEBUG, naming the device.
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {})
    fwd = UsbmuxForwarder(_UDID, socket_path=str(mux_path))
    try:
        port = fwd.start()
        mux.devices = []
        with (
            caplog.at_level(logging.WARNING),
            socket.create_connection(("127.0.0.1", port), timeout=5) as client,
        ):
            assert _read_to_eof(client) == b""
        assert any(
            _UDID in r.getMessage() and "does not list" in r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING
        )
    finally:
        fwd.close()
        mux.close()


def test_the_forwarder_can_target_a_port_the_device_chose(mux_path: Path, echo_port: int) -> None:
    # The app's `nativeZ` responder binds a port the host injected before the bridge existed, so the
    # host end is ephemeral and the tunnel targets that fixed device port instead.
    mux = _FakeUsbmuxd(mux_path, [_device(3, _UDID, "USB")], {47001: echo_port})
    fwd = UsbmuxForwarder(_UDID, device_port=47001, socket_path=str(mux_path))
    try:
        port = fwd.start()
        assert fwd.device_port == 47001 != port
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(b"GET /zorder")
            assert s.recv(4096) == b"echo:GET /zorder"
        assert socket.ntohs(mux.connects[0]["PortNumber"]) == 47001
    finally:
        fwd.close()
        mux.close()
