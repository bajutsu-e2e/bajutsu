"""Screenshot-driven recovery: clear an unexpected OS prompt, then let the run retry."""

from __future__ import annotations

import logging

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import screen_size
from bajutsu.common.orchestrator import AlertEvent
from bajutsu.common.screenshots import screenshot_bytes

from .alert_locator import AlertLocator

_logger = logging.getLogger(__name__)


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
            # because from here on --system-alert-handling is silently not handling anything.
            _logger.warning(
                "alert locator failed; blocking prompts will not be dismissed: %s",
                exc,
                exc_info=True,
            )
            return None
        if not decision.present:
            return None
        width, height = screen_size(driver)
        if width <= 0 or height <= 0:
            return None
        driver.tap_point((decision.x * width, decision.y * height))
        return AlertEvent(label=decision.label)
