"""The seam for a backend that clears a notification banner on its own (BE-0496)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BannerDismissalReporter(Protocol):
    """A backend that swipes away a notification banner covering a gesture's point, and says so.

    Separate from `InterruptionPolicyTarget`, whose drain also carries a button policy only XCUITest
    applies: a backend that clears banners itself has no such policy to accept. The orchestrator
    records each entry as an `AlertEvent` of kind `notificationBanner`, the same record the iOS
    interruption monitor's banner path produces, so the report reads alike on either platform.
    """

    def drain_banner_dismissals(self) -> list[str]:
        """The text of each banner the driver cleared since the last call, oldest first."""
