"""The `web` step: enter a WebView's DOM and run inner steps against it."""

from __future__ import annotations

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.selector import Selector

from .step import Step


class Web(_Model):
    """Enter the web context: resolve a native WebView host, then run inner steps against its DOM.

    The ``within`` selector resolves natively to exactly one WebView host: the ``WKWebView`` itself
    on iOS, usually a container the ``WebView`` fills on Android. Inner ``steps`` address the
    normalized DOM (``data-testid`` → ``Element.identifier``), not the native a11y tree.
    """

    within: Selector
    steps: list[Step]
