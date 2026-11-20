"""A heads-up notification read from SystemUI's own windows in a resident dump (BE-0496)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers import base


@dataclass(frozen=True)
class HeadsUpBanner:
    """The heads-up notification on screen when a read was taken: its frame, and what it says.

    `frame` is in the dump's raw display pixels, the same space every gesture on this backend
    addresses. `text` joins the notification's title and body, so a dismissal reaches the report
    under the notification's own words (`AlertEvent`'s label contract); empty when the row carries
    neither.
    """

    frame: base.Frame
    text: str = ""
