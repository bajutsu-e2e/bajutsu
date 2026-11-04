"""The read-side callables a driver gets from a started resident server (BE-0332)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers.adb import ActFn, ClockFetch, HierarchyFetch, TypeFn


@dataclass(frozen=True)
class ResidentChannel:
    """The two read-side callables the driver needs from a started resident server (BE-0332 Unit 3).

    `fetch` returns the current hierarchy and its read mark, blocking until the read postdates the mark
    it is passed (BE-0332 Unit 4); `clock` returns the device's current clock so the driver can anchor
    its read-lag barrier before a gesture; `act` performs a gesture on the device against an element the
    host already resolved, so no coordinate crosses; `type_text` enters text `input text` cannot carry
    into the focused field. All four close over the lease's forwarded host port.
    """

    fetch: HierarchyFetch
    clock: ClockFetch
    act: ActFn
    type_text: TypeFn
