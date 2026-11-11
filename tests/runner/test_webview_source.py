"""Which WebView source each platform's lease gets (BE-0482)."""

from __future__ import annotations

from collections.abc import Callable

from _runner import _eff, _web_eff

from bajutsu.common.config import Effective
from bajutsu.common.drivers.webview import WebViewBridge
from bajutsu.common.platform_lifecycle import WebEnvironment
from bajutsu.common.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.common.platform_lifecycle.webview_source import NO_WEBVIEW_SOURCE, http_bridge_source


def test_http_bridge_source_reserves_a_port_and_dials_the_host_port_that_reaches_it() -> None:
    reached: list[int] = []
    closed: list[int] = []

    def reach(_eff: Effective, port: int) -> tuple[int, Callable[[], None]]:
        reached.append(port)
        return port + 7, lambda: closed.append(port)

    source, device_port, release = http_bridge_source(reach, _eff())
    assert device_port is not None and reached == [device_port]
    assert isinstance(source, WebViewBridge) and source.port == device_port + 7
    release()
    assert closed == [device_port]


def test_the_simulator_gets_the_http_bridge_on_its_own_port() -> None:
    source, device_port, _ = _DeviceEnvironment("fake", "U").webview_source(_eff())
    assert isinstance(source, WebViewBridge) and source.port == device_port


def test_the_web_backend_has_no_webview_source() -> None:
    env = WebEnvironment("playwright")
    assert env.webview_source(_web_eff(base_url="http://x/")) is NO_WEBVIEW_SOURCE
