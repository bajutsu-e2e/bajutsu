"""The browsers a web target names by `runsOn.browser.kind`, and how Playwright launches each (BE-0468).

It sits below the config schema so the schema, the device readers, and the driver read one table.
"""

from __future__ import annotations

from typing import Literal, get_args

# Chrome and Edge are Playwright's chromium engine launched on an installed channel; WebKit stands in
# for Safari, which Playwright cannot drive.
BrowserKind = Literal["chromium", "chrome", "edge", "firefox", "webkit"]
BROWSER_KINDS: tuple[str, ...] = get_args(BrowserKind)

_LAUNCH: dict[str, tuple[str, str | None]] = {
    "chromium": ("chromium", None),
    "chrome": ("chromium", "chrome"),
    "edge": ("chromium", "msedge"),
    "firefox": ("firefox", None),
    "webkit": ("webkit", None),
}


def browser_launch(kind: str) -> tuple[str, str | None]:
    """The Playwright engine and channel a browser *kind* launches as.

    Raises:
        ValueError: *kind* is not one of `BROWSER_KINDS`.
    """
    if kind not in _LAUNCH:
        raise ValueError(f"invalid browser {kind!r}: use one of {', '.join(BROWSER_KINDS)}")
    return _LAUNCH[kind]
