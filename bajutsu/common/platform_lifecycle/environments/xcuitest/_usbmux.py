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
from collections.abc import Callable
from typing import Any

from bajutsu.common.capability.preflight import Check
from bajutsu.common.config import XcuitestConfig

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
# A ceiling on each usbmuxd request/reply exchange, so a mux that stops answering fails the spawn or
# the one connection instead of hanging a thread; a joined tunnel drops it (`connect`).
_HANDSHAKE_TIMEOUT = 5.0


class UsbmuxError(OSError):
    """usbmuxd is unreachable, does not list the device, or refused a request."""


class UsbmuxRefused(UsbmuxError):
    """The device port has no listener: during a cold spawn, the runner is not up yet."""


def _send(sock: socket.socket, payload: dict[str, Any], tag: int = 1) -> None:
    body = plistlib.dumps({"ClientVersionString": _PROG, "ProgName": _PROG, **payload})
    sock.sendall(_HEADER.pack(_HEADER.size + len(body), _PLIST_VERSION, _PLIST_MESSAGE, tag) + body)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except TimeoutError as exc:
            raise UsbmuxError(f"usbmuxd did not answer within {_HANDSHAKE_TIMEOUT}s") from exc
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
    sock.settimeout(_HANDSHAKE_TIMEOUT)
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
    return int(_listing(udid, socket_path)["DeviceID"])


def device_connection(udid: str, socket_path: str = USBMUXD_SOCKET) -> str:
    """How usbmuxd reaches the device `udid` (`USB` or `Network`), for a diagnostic.

    Raises:
        UsbmuxError: usbmuxd is unreachable or does not list the device.
    """
    return str(_listing(udid, socket_path).get("Properties", {}).get("ConnectionType", "unknown"))


def device_reachability(udid: str, socket_path: str = USBMUXD_SOCKET) -> tuple[bool, str]:
    """Whether the host reaches the real device `udid` over usbmuxd, and a line saying how or why not.

    For `doctor`, whose default `--udid` is a Simulator's `booted`, an alias a real device does not
    have: that case names the missing flag instead of asking usbmuxd for a device called "booted".
    """
    if udid == "booted":
        return False, "pass --udid <device udid>; a real device has no booted alias"
    try:
        return True, f"usbmuxd reaches {udid} over {device_connection(udid, socket_path)}"
    except UsbmuxError as exc:
        return False, str(exc)


def real_device_check(
    xcfg: XcuitestConfig | None,
    actuator: str,
    udid: str,
    *,
    reach: Callable[[str], tuple[bool, str]] | None = None,
) -> Check | None:
    """`doctor`'s runnability check for a real-device target, or None for any other target.

    A real device needs no booted Simulator, so this check stands in that one's place
    (`preflight.doctor_environment_checks`): the host reaches the runner, `nativeZ`, and the WebView
    bridge over usbmuxd, so a device usbmuxd does not list is not runnable.
    """
    if actuator != "xcuitest" or xcfg is None or xcfg.device_type != "device":
        return None
    ok, detail = (reach or device_reachability)(udid)
    return Check("real device reachable", ok, detail)


def _listing(udid: str, socket_path: str) -> dict[str, Any]:
    """usbmuxd's entry for `udid`, a USB attachment ahead of a network one."""
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
    return dict(matches[0])


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
        raise UsbmuxRefused(f"usbmuxd refused device port {port} on {udid} (result {result})")
    # From here the socket is the raw byte stream to the device port, which the driver keeps open
    # between calls, so it carries no idle deadline of its own.
    sock.settimeout(None)
    return sock


def _pump(src: socket.socket, dst: socket.socket) -> None:
    """Copy `src` to `dst` until either side closes, then half-close `dst` so the peer sees EOF."""
    with contextlib.suppress(OSError):
        while chunk := src.recv(_CHUNK):
            dst.sendall(chunk)
    with contextlib.suppress(OSError):
        dst.shutdown(socket.SHUT_WR)


class UsbmuxForwarder:
    """Listen on host `127.0.0.1:<port>` and tunnel each connection to a port on a device.

    `start` binds an ephemeral host port and returns it. With no `device_port`, the tunnel targets
    that same number on the device: the caller hands it to the runner as the port to bind, so one
    number names both ends and no second allocation can race the first. A `device_port` targets a
    port the device side already chose instead (the app's `nativeZ` responder). A connection the
    device refuses (nothing listening yet) is closed at once, which a health poll reads as not-ready.
    """

    def __init__(
        self, udid: str, *, device_port: int | None = None, socket_path: str = USBMUXD_SOCKET
    ) -> None:
        self._udid = udid
        self._device_port = device_port
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

    @property
    def device_port(self) -> int:
        """The device port each connection is tunnelled to."""
        return self._device_port if self._device_port is not None else self.port

    def close(self) -> None:
        """Stop accepting and drop every open tunnel; safe to call twice."""
        listener, self._listener = self._listener, None
        if listener is not None:
            # On Linux a bare close() neither wakes the thread blocked in accept() nor stops the
            # socket listening; shutdown() does both (macOS answers it with ENOTCONN, suppressed).
            with contextlib.suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
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
            device = connect(self._udid, self.device_port, self._socket_path)
        except UsbmuxRefused as exc:
            # The expected answer to nearly every `/health` probe while the runner starts.
            _logger.debug("usbmux forward %s → device: %s", self.port, exc)
            client.close()
            return
        except OSError as exc:
            # The bridge itself is gone (device unplugged, untrusted, rebooted): the driver only
            # sees EOF, so this is the one place the real reason is reported.
            _logger.warning("usbmux forward %s → device %s failed: %s", self.port, self._udid, exc)
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
