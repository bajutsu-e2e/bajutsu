"""One device-side actuation request: what to do, and which element to do it to."""

from __future__ import annotations

from dataclasses import dataclass

from ._shared import NodeIdentity


@dataclass(frozen=True)
class ActRequest:
    """One device-side actuation: what to do, and which element to do it to.

    The host has already decided *which* element — `resolve_unique` ran here, so an ambiguous selector
    failed before this was built. What crosses to the device is that element's identity, plus where it
    sat among the nodes sharing that identity (`index` of `count`), so the device can confirm it is
    looking at the same screen before it injects. No coordinate crosses: the device reads the bounds
    itself, microseconds before the touch, from a dump of its own.
    """

    kind: str  # "tap" | "longPress" | "doubleTap" | "setPickerValue" (BE-0480, its own endpoint)
    identity: NodeIdentity
    index: int  # the element's ordinal among the nodes sharing `identity`, in document order
    count: int  # how many such nodes the host saw — the device refuses if its own count differs
    # The mark the device's *own* pre-injection bounds read must postdate: the previous gesture's
    # actuation mark, so those bounds describe a screen that gesture has already reached. None when
    # no gesture is still outstanding — which, since `_device_act` settles first, is the common case.
    # Never the clock as of building this request: no event can postdate that on a settled screen, so
    # the device would spend its whole postdate budget on every gesture (BE-0407 unit 16).
    since: float | None
    duration_ms: int | None  # press-and-hold length, for "longPress"
