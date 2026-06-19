"""Vision tab locator — find a tab bar's items the accessibility tree can't address (BE-0038).

A tab bar is usually exposed in the tree as a `tab`-trait button with an identifier, and the
crawl taps those directly (`candidate_actions` orders them tabs-first). But some apps build a
custom tab bar from images with no identifier and no `tab` trait, so idb's query can't address
the individual tabs — the same blind spot the system-alert guard ([`alerts.py`](alerts.py))
works around. When the tree exposes no tabs, this locator takes a screenshot, asks Claude vision
for the tab bar items, and returns each as a normalized [0,1] coordinate the crawl turns into a
replayable coordinate tap (`Action(kind="tap_point")`).

Like the alert guard it only decides *where to tap* — the guide layer's "what to try". Screen
identity, transitions and crashes stay deterministic in [`crawl.py`](crawl.py), and the tap is
replayed by its stored coordinate (never re-located), so the crawl is never a verdict
(prime directive #1). The locator is injectable: production uses Claude vision; tests inject a
deterministic fake, mirroring how the alert guard and the action proposer are tested.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

from bajutsu.alerts import _fraction, _png_size
from bajutsu.drivers import base

TAB_LOCATOR_MODEL = "claude-opus-4-8"


@dataclass
class TabTarget:
    """One tab bar item to try: its center as a fraction [0,1] of the screen, plus its visible
    text (for logging) when readable."""

    x: float
    y: float
    label: str = ""


class TabLocator(Protocol):
    """Given a screenshot, return the tab bar's items (empty when there is no addressable tab bar)."""

    def locate(self, screenshot_png: bytes) -> list[TabTarget]: ...


def tree_exposes_tabs(elements: list[base.Element]) -> bool:
    """Whether the accessibility tree already exposes tab controls (a `tab`-trait element). When it
    does, the deterministic `candidate_actions` taps them directly and the vision locator is
    skipped; when it doesn't, an app with a custom tab bar needs the vision fallback."""
    return any("tab" in (el.get("traits") or []) for el in elements)


# --- Claude vision locator (the production brain) ---

_SYSTEM = """You locate the tab bar of an iOS app for an automated crawl. You are given a \
screenshot of the screen. A "tab bar" is the row of top-level sections — usually along the \
bottom edge — that switches the whole view (e.g. Home / Search / Profile). It is NOT a navigation \
bar, a toolbar, or in-content buttons.

Call the tool `find_tabs` exactly once:
- If there is no tab bar on this screen, return an empty `tabs` array.
- Otherwise return one entry per tab, left to right, each with x,y as the CENTER of the tab in \
PIXEL coordinates of the screenshot. The image's exact pixel width and height are stated with the \
request: x runs from 0 at the left edge to width at the right, y from 0 at the top to height at \
the bottom. These phone screenshots are tall, so judge the vertical position carefully against the \
stated height — a bottom tab bar sits near the bottom. Include the tab's visible text in `label` \
when it has one.
- You only report where the tabs are. You never decide pass/fail."""

_FIND_TABS_TOOL: list[dict[str, Any]] = [
    {
        "name": "find_tabs",
        "description": "Report the app's tab bar items (empty when there is no tab bar).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tabs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number", "description": "tab center x in pixels"},
                            "y": {"type": "number", "description": "tab center y in pixels"},
                            "label": {"type": "string", "description": "the tab's visible text"},
                        },
                        "required": ["x", "y"],
                    },
                }
            },
            "required": ["tabs"],
        },
    }
]


def _targets_of(message: Any, width: int, height: int) -> list[TabTarget]:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None:
        return []
    out: list[TabTarget] = []
    for item in tool_use.input.get("tabs") or []:
        if not isinstance(item, dict) or item.get("x") is None or item.get("y") is None:
            continue
        out.append(
            TabTarget(
                x=_fraction(float(item["x"]), width),
                y=_fraction(float(item["y"]), height),
                label=str(item.get("label", "")),
            )
        )
    return out


class ClaudeTabLocator:
    """TabLocator backed by Claude vision; `anthropic` is lazy-imported so the module loads without
    the SDK or an API key, like the alert locator."""

    def __init__(self, client: Any = None, model: str = TAB_LOCATOR_MODEL) -> None:
        self._client = client
        self._model = model

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def locate(self, screenshot_png: bytes) -> list[TabTarget]:
        client = self._ensure_client()
        width, height = _png_size(screenshot_png)
        text = (
            f"Find the tab bar. The screenshot is {width}x{height} pixels (width x height); give "
            "each tab center as pixel coordinates within that range."
        )
        message = client.messages.create(
            model=self._model,
            max_tokens=512,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=_FIND_TABS_TOOL,
            tool_choice={"type": "any"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.standard_b64encode(screenshot_png).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": text},
                    ],
                }
            ],
        )
        return _targets_of(message, width, height)
