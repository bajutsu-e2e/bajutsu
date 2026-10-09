"""Host → real-device TCP bridge over usbmuxd, for the XCUITest runner channel (BE-0238 follow-up).

The runner binds its HTTP server to the *device's* loopback (`HTTPServer.swift`), and the driver
dials the *host's*. On the Simulator those are one interface; on a real device nothing joins them.
This is the iOS counterpart of the `adb forward` the resident Android channel sets up: a listener on
the host's `127.0.0.1` whose every accepted connection is tunnelled to the same port on the device
through usbmuxd's `Connect` request — the same mux `iproxy` and Xcode itself ride. It speaks the
usbmuxd plist protocol directly over `/var/run/usbmuxd`, so it needs no extra host tool or package.
"""

from __future__ import annotations

import contextlib
import logging
import plistlib
import socket
import struct
import threading
from typing import Any

_logger = logging.getLogger(__name__)

USBMUXD_SOCKET = "/var/run/usbmuxd"

# usbmuxd's framing: little-endian length (header included), protocol version, message type, tag.
# Version 1 with type 8 is the plist dialect every current usbmuxd speaks.
_HEADER = struct.Struct("<IIII")
_PLIST_VERSION = 1
_PLIST_MESSAGE = 8
_PROG = "bajutsu"
# `Connect` result 0 is success; 3 is "connection refused" — the device port has no listener yet,
# which during a cold spawn just means the runner is still starting.
_RESULT_OK = 0
_CHUNK = 64 * 1024


class UsbmuxError(OSError):
    """usbmuxd is unreachable, does not list the device, or refused a request."""


def _send(sock: socket.socket, payload: dict[str, Any], tag: int = 1) -> None:
    body = plistlib.dumps({"ClientVersionString": _PROG, "ProgName": _PROG, **payload})
    sock.sendall(_HEADER.pack(_HEADER.size + len(body), _PLIST_VERSION, _PLIST_MESSAGE, tag) + body)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise UsbmuxError("usbmuxd closed the connection mid-reply")
        buf += chunk
    return bytes(buf)


def _recv(sock: socket.socket) -> dict[str, Any]:
    length, _, _, _ = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    reply = plistlib.loads(_recv_exact(sock, length - _HEADER.size))
    if not isinstance(reply, dict):
        raise UsbmuxError(f"usbmuxd sent a non-dictionary reply: {reply!r}")
    return reply


def _mux_socket(socket_path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except OSError as exc:
        sock.close()
        raise UsbmuxError(f"cannot reach usbmuxd at {socket_path}: {exc}") from exc
    return sock


def device_id(udid: str, socket_path: str = USBMUXD_SOCKET) -> int:
    """usbmuxd's handle for the device `udid`, preferring a USB connection over a network one.

    The handle is per attachment — it changes when the cable is replugged — so callers resolve it
    per connection rather than caching it for a whole run.

    Raises:
        UsbmuxError: usbmuxd is unreachable or does not list the device.
    """
    with _mux_socket(socket_path) as sock:
        _send(sock, {"MessageType": "ListDevices"})
        listed = _recv(sock).get("DeviceList", [])
    # usbmuxd spells a modern udid with its hyphen and a legacy one without; compare both bare.
    wanted = udid.replace("-", "").upper()
    matches = [
        d
        for d in listed
        if str(d.get("Properties", {}).get("SerialNumber", "")).replace("-", "").upper() == wanted
    ]
    if not matches:
        raise UsbmuxError(f"usbmuxd does not list device {udid}; is it attached and trusted?")
    matches.sort(key=lambda d: d.get("Properties", {}).get("ConnectionType") != "USB")
    return int(matches[0]["DeviceID"])


def connect(udid: str, port: int, socket_path: str = USBMUXD_SOCKET) -> socket.socket:
    """A socket already joined to TCP `port` on the device's loopback.

    Raises:
        UsbmuxError: the device is not listed, or usbmuxd refused the connection (nothing listening).
    """
    did = device_id(udid, socket_path)
    sock = _mux_socket(socket_path)
    try:
        # usbmuxd takes the port in network byte order inside a host-order integer field.
        _send(sock, {"MessageType": "Connect", "DeviceID": did, "PortNumber": socket.htons(port)})
        result = _recv(sock).get("Number")
    except BaseException:
        sock.close()
        raise
    if result != _RESULT_OK:
        sock.close()
        raise UsbmuxError(f"usbmuxd refused device port {port} on {udid} (result {result})")
    # From here the socket is the raw byte stream to the device port.
    return sock


def _pump(src: socket.socket, dst: socket.socket) -> None:
    """Copy `src` to `dst` until either side closes, then half-close `dst` so the peer sees EOF."""
    with contextlib.suppress(OSError):
        while chunk := src.recv(_CHUNK):
            dst.sendall(chunk)
    with contextlib.suppress(OSError):
        dst.shutdown(socket.SHUT_WR)


class UsbmuxForwarder:
    """Listen on host `127.0.0.1:<port>` and tunnel each connection to the same port on a device.

    `start` binds an ephemeral host port and returns it; the caller hands that number to the
    runner as the port to bind on the device, so one number names both ends and no second
    allocation can race the first. A connection the device refuses (the runner is not listening yet)
    is closed at once, which the driver's health poll reads as not-ready and retries.
    """

    def __init__(self, udid: str, *, socket_path: str = USBMUXD_SOCKET) -> None:
        self._udid = udid
        self._socket_path = socket_path
        self._listener: socket.socket | None = None
        self._open: set[socket.socket] = set()
        self._lock = threading.Lock()
        self.port = 0

    def start(self) -> int:
        """Resolve the device, bind the host listener, and start accepting; return the port.

        The device is resolved up front so a detached or untrusted device fails the spawn with a
        clear reason instead of as a startup timeout.

        Raises:
            UsbmuxError: usbmuxd is unreachable or does not list the device.
        """
        device_id(self._udid, self._socket_path)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        self._listener = listener
        self.port = listener.getsockname()[1]
        threading.Thread(
            target=self._accept_loop, name=f"usbmux-fwd-{self.port}", daemon=True
        ).start()
        return self.port

    def close(self) -> None:
        """Stop accepting and drop every open tunnel; safe to call twice."""
        listener, self._listener = self._listener, None
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.close()
        with self._lock:
            stale, self._open = self._open, set()
        for sock in stale:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                sock.close()

    def _accept_loop(self) -> None:
        listener = self._listener
        while listener is not None:
            try:
                client, _ = listener.accept()
            except OSError:
                return  # closed by `close()`
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        try:
            device = connect(self._udid, self.port, self._socket_path)
        except OSError as exc:
            _logger.debug("usbmux forward %s → device: %s", self.port, exc)
            client.close()
            return
        with self._lock:
            if self._listener is None:  # closed while connecting
                client.close()
                device.close()
                return
            self._open.update((client, device))
        upstream = threading.Thread(target=_pump, args=(client, device), daemon=True)
        upstream.start()
        _pump(device, client)
        upstream.join()
        with self._lock:
            self._open.difference_update((client, device))
        client.close()
        device.close()
