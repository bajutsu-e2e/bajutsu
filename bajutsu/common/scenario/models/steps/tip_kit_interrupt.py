"""One `interrupts` entry that turns the TipKit guard on or off (BE-0473)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model


class TipKitInterrupt(_Model):
    """An `interrupts` entry for Apple's TipKit popovers, iOS only.

    `dismiss` clears a tip that blocks a step; `leave` keeps it, which a scenario writes to turn off
    its target's entry when a tip is the subject of its own assertion. Absent from both the scenario
    and the target means off.
    """

    tip_kit: Literal["dismiss", "leave"] = Field(alias="tipKit")
