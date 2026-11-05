"""Turn a requirement into the one-line remedy a user can act on."""

from __future__ import annotations

from bajutsu.common.devices.browsers import browser_launch

from ._shared import InstallMethod
from .brew import Brew
from .extra import Extra
from .manual import Manual
from .playwright import Playwright
from .tool import Tool


def remedy(method: InstallMethod) -> str:
    """The one-line remedy for an install method — the command to run, or a manual hint verbatim."""
    match method:
        case Extra(name):
            return f"`uv sync --extra {name}`"
        case Brew(formula):
            return f"`brew install {formula}`"
        case Playwright(browser):
            return f"`uv run playwright install {browser}`"
        case Manual(hint):
            return hint
    # The match is exhaustive over InstallMethod; this explicit terminal keeps every path
    # returning or raising (CodeQL flags a bare implicit fall-through) and guards a future variant.
    raise AssertionError(f"unhandled InstallMethod: {method!r}")  # pragma: no cover


def playwright_browser(engine: str) -> Tool:
    """The browser tool for a `runsOn.browser.kind`, built on demand rather than baked in.

    The browser is chosen per run, so it is not a static entry in the web backend's tool list — a
    `firefox` run needs `firefox`, not `chromium`. Chrome and Edge are the host's own installs that
    Playwright launches on a channel (BE-0468): installing one would change a system browser, so
    they are left to the user rather than to `playwright install`.
    """
    _engine, channel = browser_launch(engine)
    if channel is not None:
        name = _CHANNEL_BROWSERS.get(engine, engine)
        return Tool(engine, Manual(f"install {name} on this host; Playwright launches it as is"))
    return Tool(engine, Playwright(engine))


_CHANNEL_BROWSERS = {"chrome": "Google Chrome", "edge": "Microsoft Edge"}
