"""System-alert guard — detect and dismiss OS prompts the app cannot see.

idb's accessibility query is scoped to the foreground app, so SpringBoard-level
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

import base64
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from simyoke.drivers import base

LOCATOR_MODEL = "claude-opus-4-8"


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


def _screenshot_png(driver: base.Driver) -> bytes | None:
    """Capture a PNG of the current screen as bytes (best-effort)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        data = Path(path).read_bytes()
        Path(path).unlink(missing_ok=True)
        return data or None
    except Exception:  # noqa: BLE001 — a missing screenshot just means "cannot guard"
        return None


def _screen_points(driver: base.Driver) -> base.Point:
    """Full-screen size in points = the largest element frame (the app window).

    Even when a SpringBoard alert collapses the tree to one node, that node is the
    app window and its frame spans the whole screen in point space.
    """
    frames = [el["frame"] for el in driver.query()]
    width = max((f[2] for f in frames), default=0.0)
    height = max((f[3] for f in frames), default=0.0)
    return (width, height)


class SystemAlertGuard:
    """Screenshot-driven recovery: clear an unexpected OS prompt, then let the run retry."""

    def __init__(self, locator: AlertLocator, instruction: str | None = None) -> None:
        self._locator = locator
        self._instruction = instruction

    def dismiss(self, driver: base.Driver) -> bool:
        """Tap to clear a blocking prompt if one is on screen. True if it acted."""
        png = _screenshot_png(driver)
        if png is None:
            return False
        decision = self._locator.locate(png, self._instruction)
        if not decision.present:
            return False
        width, height = _screen_points(driver)
        if width <= 0 or height <= 0:
            return False
        driver.tap_point((decision.x * width, decision.y * height))
        return True


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

LOCATOR_TOOL: list[dict[str, Any]] = [
    {
        "name": "resolve_alert",
        "description": "Report whether a blocking system prompt is present and where to tap.",
        "input_schema": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "x": {"type": "number", "description": "button center x in pixels"},
                "y": {"type": "number", "description": "button center y in pixels"},
                "label": {"type": "string", "description": "the button's text"},
            },
            "required": ["present"],
        },
    }
]


def _png_size(png: bytes) -> tuple[int, int]:
    """(width, height) in pixels from the PNG IHDR header, or (0, 0) if not parseable."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return (0, 0)


def _fraction(value: float, size: int) -> float:
    """Map a coordinate to [0,1]: a pixel value (>1) over a known size, else as-is."""
    frac = value / size if size > 0 and value > 1.0 else value
    return min(1.0, max(0.0, frac))


def _decision_of(message: Any, width: int, height: int) -> AlertDecision:
    tool_use = next((b for b in message.content if b.type == "tool_use"), None)
    if tool_use is None or not tool_use.input.get("present"):
        return AlertDecision(present=False)
    args = tool_use.input
    raw_x, raw_y = args.get("x"), args.get("y")
    return AlertDecision(
        present=True,
        x=0.5 if raw_x is None else _fraction(float(raw_x), width),
        y=0.5 if raw_y is None else _fraction(float(raw_y), height),
        label=str(args.get("label", "")),
    )


class ClaudeAlertLocator:
    """AlertLocator backed by Claude vision; `anthropic` is lazy-imported."""

    def __init__(self, client: Any = None, model: str = LOCATOR_MODEL) -> None:
        self._client = client
        self._model = model

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        client = self._ensure_client()
        width, height = _png_size(screenshot_png)
        text = (
            "Clear the blocking system prompt if one is present. "
            f"The screenshot is {width}x{height} pixels (width x height); give the "
            "button center as pixel coordinates within that range."
        )
        if instruction:
            text += f"\nInstruction for the prompt: {instruction}"
        message = client.messages.create(
            model=self._model,
            max_tokens=512,
            system=[
                {"type": "text", "text": LOCATOR_SYSTEM, "cache_control": {"type": "ephemeral"}}
            ],
            tools=LOCATOR_TOOL,
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
        return _decision_of(message, width, height)
