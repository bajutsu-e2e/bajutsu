"""What a lease reads a `web:` block's WebView DOM through, chosen per platform (BE-0482).

The run loop sees one `DomSource` whatever the platform; the environment decides which. iOS dials
the BajutsuKit bridge, an HTTP server the app binds on a port it is told at launch. Android needs no
app-side server — its WebViews already serve CDP — so it names no port. The web backend's driver
owns the page itself, so it has no WebView source at all.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import NamedTuple

from bajutsu.common.config import Effective
from bajutsu.common.drivers.webview import DomSource, WebViewBridge


class WebViewSource(NamedTuple):
    """One lease's WebView source and what launching and releasing it involve.

    `device_port` is the port the app must bind for the source to reach it — sent to the app as
    `BAJUTSU_WEBVIEW_PORT` — or None when the app binds nothing. `release` closes whatever the
    allocation opened; it never raises.
    """

    source: DomSource | None
    device_port: int | None
    release: Callable[[], None]


def _no_release() -> None:
    pass


NO_WEBVIEW_SOURCE = WebViewSource(None, None, _no_release)


def http_bridge_source(
    reach_device_port: Callable[[Effective, int], tuple[int, Callable[[], None]]],
    eff: Effective,
) -> WebViewSource:
    """The iOS shape: reserve a port for the in-app bridge, and the host port that reaches it.

    The port comes from the OS ephemeral range, one per lease, so parallel devices never contend;
    `reach_device_port` is the environment's own bridge to it (a usbmuxd tunnel on a real device).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    host_port, release = reach_device_port(eff, port)
    return WebViewSource(WebViewBridge(port=host_port), port, release)
