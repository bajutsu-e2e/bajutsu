"""System-alert guard — detect and dismiss OS prompts the app cannot see.

The iOS accessibility query is scoped to the foreground app, so SpringBoard-level
prompts (e.g. the iOS "Save Password?" alert) are invisible to it and silently
block a run: the app's element tree collapses to a single window node. This guard
takes a screenshot, asks a vision locator where to tap, and taps it by coordinate.
The locator dismisses the prompt by default, or follows a specific instruction
when one is configured for it.

The locator is injectable: production uses Claude vision; tests and offline runs
inject a deterministic one. Coordinates are image-normalized [0,1] so they map to
the device's point-space screen regardless of the screenshot's pixel scale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from bajutsu.agents.ai_config import AiConfig
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
from bajutsu.analytics import usage
from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.evidence.redaction import Redactor
from bajutsu.orchestrator import AlertEvent
from bajutsu.screenshots import fraction, png_size, screenshot_bytes

# Sonnet over Opus: this fires mid-wait (BE-0269), so its round-trip latency is on the run's
# critical path — a locate-a-button task doesn't need Opus's extra reasoning depth.
LOCATOR_MODEL = "claude-sonnet-5"

_logger = logging.getLogger(__name__)


@dataclass
class AlertDecision:
    """How to clear a detected prompt: the button center in normalized coords."""

    present: bool
    x: float = 0.0  # button center x as a fraction [0,1] of screen width
    y: float = 0.0  # button center y as a fraction [0,1] of screen height
    label: str = ""  # the button's text, for logging


class AlertLocator(Protocol):
    """Given a screenshot, decide whether a blocking prompt is up and where to tap."""

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision: ...


class SystemAlertGuard:
    """Screenshot-driven recovery: clear an unexpected OS prompt, then let the run retry."""

    def __init__(self, locator: AlertLocator, instruction: str | None = None) -> None:
        self._locator = locator
        self._instruction = instruction

    def dismiss(self, driver: base.Driver) -> AlertEvent | None:
        """Tap to clear a blocking prompt if one is on screen.

        Returns the AlertEvent it dismissed (the button it tapped), or None when nothing
        on screen needed clearing.
        """
        png = screenshot_bytes(driver)
        if png is None:
            return None
        try:
            decision = self._locator.locate(png, self._instruction)
        except Exception as exc:
            # Best-effort: the guard is on by default, so it must never crash a run — but warn,
            # because from here on --alert-handling is silently not handling anything.
            _logger.warning(
                "alert locator failed; blocking prompts will not be dismissed: %s",
                exc,
                exc_info=True,
            )
            return None
        if not decision.present:
            return None
        width, height = screen_size_from_elements(driver.query())
        if width <= 0 or height <= 0:
            return None
        driver.tap_point((decision.x * width, decision.y * height))
        return AlertEvent(label=decision.label)


# --- Claude vision locator (the production brain) ---

LOCATOR_SYSTEM = """You clear an unexpected iOS system prompt that is blocking an \
automated UI test. You are given a screenshot of the screen. A "system prompt" is an \
OS-level alert, action sheet, or dialog that is NOT part of the app under test — for \
example "Save Password?", a notification-permission alert, "Allow Paste", or a \
location-access request.

Call the tool `resolve_alert` exactly once:
- If no such prompt is present (the screenshot shows only the app), set present=false.
- If a prompt is present, set present=true and return x,y as the CENTER of the button \
to tap, in PIXEL coordinates of the screenshot. The image's exact pixel width and \
height are stated with the request: x runs from 0 at the left edge to width at the \
right, y from 0 at the top to height at the bottom. These phone screenshots are tall, \
so judge the vertical position carefully against the stated height.
- By default choose the dismissive, least-destructive button (e.g. "Not Now", \
"Don't Allow", "Cancel", "Close"). If an instruction is provided, follow it instead \
and tap the button it names."""

LOCATOR_TOOL: list[ToolDef] = [
    ToolDef(
        name="resolve_alert",
        description="Report whether a blocking system prompt is present and where to tap.",
        input_schema={
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "x": {"type": "number", "description": "button center x in pixels"},
                "y": {"type": "number", "description": "button center y in pixels"},
                "label": {"type": "string", "description": "the button's text"},
            },
            "required": ["present"],
        },
    )
]


def _decision_of(response: MessageResponse, width: int, height: int) -> AlertDecision:
    tool_use = response.first_tool_use()
    if tool_use is None or not tool_use.input.get("present"):
        return AlertDecision(present=False)
    args = tool_use.input
    raw_x, raw_y = args.get("x"), args.get("y")
    return AlertDecision(
        present=True,
        x=0.5 if raw_x is None else fraction(float(raw_x), width),
        y=0.5 if raw_y is None else fraction(float(raw_y), height),
        label=str(args.get("label", "")),
    )


class ClaudeAlertLocator(ClaudeBackedAgent):
    """AlertLocator backed by Claude vision, through the vendor-neutral backend (BE-0104)."""

    def __init__(
        self,
        backend: AiBackend | None = None,
        model: str | None = None,
        *,
        ai: AiConfig | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        super().__init__(
            backend=backend, ai=ai, default_model=LOCATOR_MODEL, model=model, redactor=redactor
        )

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        width, height = png_size(screenshot_png)
        text = (
            "Clear the blocking system prompt if one is present. "
            f"The screenshot is {width}x{height} pixels (width x height); give the "
            "button center as pixel coordinates within that range."
        )
        if instruction:
            # The instruction may be user-supplied (--alert-instruction); mask secrets before it
            # reaches the model (BE-0047). The screenshot beside it cannot be pixel-masked.
            if self._redactor is not None:
                instruction = self._redactor.redact_text(instruction)
            text += f"\nInstruction for the prompt: {instruction}"
        response = self._ensure_backend().create_message(
            MessageRequest(
                system=LOCATOR_SYSTEM,
                messages=[
                    Message(
                        role="user",
                        content=[ImagePart(data=screenshot_png), TextPart(text=text)],
                    )
                ],
                tools=LOCATOR_TOOL,
                tool_choice=AnyTool(),
                model=self._model,
                max_tokens=512,
            )
        )
        self._record_usage(response, usage.CATEGORY_ALERT)
        return _decision_of(response, width, height)
