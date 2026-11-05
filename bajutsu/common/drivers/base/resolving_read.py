"""What one request that resolves alerts returned (BE-0461)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .drained_interruptions import DrainedInterruptions
from .element import Element

# How much of the resolution a read runs: `true` checks SpringBoard, takes the snapshot, and answers
# an in-tree prompt; `inTree` skips the SpringBoard check, for a `handleSystemAlert` step's own reads,
# which must leave that step's alert to the step.
ResolveMode = Literal["true", "inTree"]

# What `resolve_system_alert` did with the SpringBoard alert showing.
SystemAlertResolutionStatus = Literal["answered", "changed", "absent", "unidentified", "pending"]


@dataclass(frozen=True)
class ResolvingRead:
    """The tree a resolving read returned, with the records that same request drained.

    Attributes:
        elements: The application's tree, read after any alert this request answered.
        drained: Every record the backend held, drained into this reply. The backend also keeps them
            for the drain that consumes them, so reading them here reports nothing.
        springboard_checked: This request's own SpringBoard check ran and found no alert, and its
            in-tree pass, when it re-checked, found none either. A fact of this request, never a
            remembered one.
        withheld: The pacing memo kept this request from pressing a showing already answered,
            inside the re-tap delay.
    """

    elements: list[Element]
    drained: DrainedInterruptions
    springboard_checked: bool = False
    withheld: bool = False


@dataclass(frozen=True)
class SystemAlertResolution:
    """What `resolve_system_alert` did, with the records that same request drained.

    Attributes:
        status: `answered` when a rule identified the alert and its button was pressed; `changed`
            when the alert no longer offered the labels the step read; `absent` when none was up;
            `unidentified` when no rule identifies it; `pending` when a rule did but nothing landed.
        drained: Every record the backend held, drained into this reply, as `ResolvingRead.drained`.
    """

    status: SystemAlertResolutionStatus
    drained: DrainedInterruptions
