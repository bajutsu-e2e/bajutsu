"""WebView DOM sources — the channels a `web:` block reads a WebView's DOM through.

iOS reaches the WebView through the BajutsuKit bridge, an HTTP server inside the app
(`WebViewBridge`); Android reaches it over the Chrome DevTools Protocol socket every debuggable
WebView already serves (`CdpDomSource`, BE-0482). `WebContextDriver` resolves selectors against
either one unchanged.
"""

from .cdp import WebViewTransportError
from .cdp_dom_source import CdpDomSource
from .dom_source import DomSource
from .web_context_driver import _UNIT as _UNIT
from .web_context_driver import WebContextDriver
from .web_view_bridge import WebViewBridge

__all__ = [
    "CdpDomSource",
    "DomSource",
    "WebContextDriver",
    "WebViewBridge",
    "WebViewTransportError",
]
