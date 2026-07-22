"""Vision tab locator — find a tab bar's items the accessibility tree can't address (BE-0038).

A tab bar is usually exposed in the tree as a `tab`-trait button with an identifier, and the
crawl taps those directly (`candidate_actions` orders them tabs-first). But some apps build a
custom tab bar from images with no identifier and no `tab` trait, so the accessibility tree can't address
the individual tabs — the same blind spot the system-alert guard ([`alerts.py`](alerts.py))
works around. When the tree exposes no tabs, this locator takes a screenshot, asks Claude vision
for the tab bar items, and returns each as a normalized [0,1] coordinate the crawl turns into a
replayable coordinate tap (`Action(kind="tap_point")`).

Like the alert guard it only decides *where to tap* — the guide layer's "what to try". Screen
identity, transitions and crashes stay deterministic in [`core.py`](core.py), and the tap is
replayed by its stored coordinate (never re-located), so the crawl is never a verdict
(prime directive #1). The locator is injectable: production uses Claude vision; tests inject a
deterministic fake, mirroring how the alert guard and the action proposer are tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bajutsu.agents.ai_config import AiConfig, language_instruction
from bajutsu.agents.claude_backed import ClaudeBackedAgent
from bajutsu.ai import (
    AiBackend,
    AnyTool,
    ImagePart,
    Message,
    MessageRequest,
    MessageResponse,
    TextPart,
    ToolDef,
)
from bajutsu.ai.prompts import NEVER_JUDGE_BOUNDARY
from bajutsu.drivers import base
from bajutsu.screenshots import fraction, png_size

TAB_LOCATOR_MODEL = "claude-opus-4-8"


@dataclass
class TabTarget:
    """One tab bar item to try: its center as a fraction [0,1] of the screen, plus its visible text (for logging) when readable."""

    x: float
    y: float
    label: str = ""


class TabLocator(Protocol):
    """Given a screenshot, return the tab bar's items (empty when there is no addressable tab bar)."""

    def locate(self, screenshot_png: bytes) -> list[TabTarget]: ...


# The accessibility label iOS auto-assigns a tab bar. iOS surfaces a SwiftUI `TabView` as a single
# container carrying this label (observed: no identifier, trait `group`) with no per-tab children —
# so the bar is on screen and tappable, but its individual tabs can't be addressed from the tree.
_TAB_BAR_LABEL = "tab bar"


def _is_tab(element: base.Element) -> bool:
    """Whether an element is a tab control named as such (`tab`, or a `tabBar` container)."""
    traits = element.get("traits") or []
    return "tab" in traits or "tabBar" in traits


def addressable_tabs(elements: list[base.Element]) -> bool:
    """Whether individual tabs are already tappable from the tree — so no vision is needed.

    Firing vision then would just duplicate those taps. Today: a tab element carrying an
    identifier, which the deterministic `candidate_actions` taps directly. UIKit support is
    provisional — see `_uikit_addressable_tabs`.
    """
    return any(_is_tab(el) and el.get("identifier") for el in elements) or _uikit_addressable_tabs(
        elements
    )


def _uikit_addressable_tabs(_elements: list[base.Element]) -> bool:
    """UIKit tab bar — provisional stub, the single place to complete once we have real UIKit tab-bar data.

    Unlike SwiftUI's opaque "Tab Bar" group, a UIKit `UITabBar` exposes each tab as its own element
    (likely a `button` with the tab's title as its label, possibly an identifier), so its tabs are
    usually addressable by selector and the deterministic guide / proposer can tap them without
    vision. We haven't yet confirmed its exact representation, so this recognizes nothing for now
    (leaving the vision fallback in charge). To complete UIKit support: capture the accessibility
    tree of a UIKit tab bar, then recognize its tab elements here (by trait / label
    / id) — `addressable_tabs` and `needs_vision_tabs` pick the result up automatically.
    """
    return False  # TODO(BE-0038): recognize UIKit UITabBarButton elements once its tree output is known


def tab_bar_present(elements: list[base.Element]) -> bool:
    """Whether a tab bar is on screen at all.

    A tab / tabBar element, or the container iOS labels "Tab Bar" (its auto-assigned accessibility
    label) — how iOS surfaces a SwiftUI TabView, as a lone `group` with that label and no
    addressable per-tab children.
    """
    for el in elements:
        if _is_tab(el):
            return True
        if (el.get("label") or "").strip().lower() == _TAB_BAR_LABEL:
            return True
    return False


def needs_vision_tabs(elements: list[base.Element]) -> bool:
    """The one case the vision locator should fire: a tab bar present but its tabs unaddressable from the tree, keeping vision off ordinary screens and id-tappable bars."""
    return tab_bar_present(elements) and not addressable_tabs(elements)


# --- Claude vision locator (the production brain) ---

_SYSTEM = f"""You locate the tab bar of an iOS app for an automated crawl. You are given a \
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
- You only report where the tabs are. {NEVER_JUDGE_BOUNDARY}"""

_FIND_TABS_TOOL: list[ToolDef] = [
    ToolDef(
        name="find_tabs",
        description="Report the app's tab bar items (empty when there is no tab bar).",
        input_schema={
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
    )
]


def _targets_of(response: MessageResponse, width: int, height: int) -> list[TabTarget]:
    tool_use = response.first_tool_use()
    if tool_use is None:
        return []
    out: list[TabTarget] = []
    for item in tool_use.input.get("tabs") or []:
        if not isinstance(item, dict) or item.get("x") is None or item.get("y") is None:
            continue
        out.append(
            TabTarget(
                x=fraction(float(item["x"]), width),
                y=fraction(float(item["y"]), height),
                label=str(item.get("label", "")),
            )
        )
    return out


class ClaudeTabLocator(ClaudeBackedAgent):
    """TabLocator backed by Claude vision, through the vendor-neutral backend (BE-0104)."""

    def __init__(
        self,
        backend: AiBackend | None = None,
        model: str | None = None,
        *,
        ai: AiConfig | None = None,
    ) -> None:
        super().__init__(backend=backend, ai=ai, default_model=TAB_LOCATOR_MODEL, model=model)
        self._lang = language_instruction(ai)  # output-language suffix, empty for `auto` (BE-0188)

    def locate(self, screenshot_png: bytes) -> list[TabTarget]:
        width, height = png_size(screenshot_png)
        text = (
            f"Find the tab bar. The screenshot is {width}x{height} pixels (width x height); give "
            "each tab center as pixel coordinates within that range."
        )
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=_SYSTEM + self._lang,
                messages=[
                    Message(
                        role="user",
                        content=[ImagePart(data=screenshot_png), TextPart(text=text)],
                    )
                ],
                tools=_FIND_TABS_TOOL,
                tool_choice=AnyTool(),
                model=self._model,
                max_tokens=512,
            )
        )
        # reporting only (BE-0104) — never on the pass/fail path
        self._record_usage(response)
        return _targets_of(response, width, height)
