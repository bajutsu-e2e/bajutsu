"""The bridge seam the WebView driver reads through, satisfied by the client and by fakes."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from bajutsu.common.drivers.base import Element, Frame, Point


class DomSource(Protocol):
    """Minimal bridge interface — satisfied by WebViewBridge, CdpDomSource, and test fakes."""

    def session(self, webview_id: str, frame: Frame) -> AbstractContextManager[None]:
        """The span of one `web:` block, entered before its first step and exited after its last.

        `frame` is the native element the full `within` selector resolved, in device pixels — the
        only handle a source with no native id (CDP) has on which WebView the block means (BE-0482).
        The context exits whether the block passes or fails.
        """

    def query_dom(self, webview_id: str) -> list[Element]: ...
    def tap_element(self, webview_id: str, point: Point) -> None: ...
    def type_text(self, webview_id: str, text: str) -> None: ...
    def scroll_to(self, webview_id: str, element_id: str) -> None: ...
