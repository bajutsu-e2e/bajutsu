"""The read-side callables a driver gets from a started resident server (BE-0332)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers.adb import (
    ActFn,
    ChannelRecovery,
    ClockFetch,
    HierarchyFetch,
    PickerFn,
    TypeFn,
)


@dataclass(frozen=True)
class ResidentChannel:
    """The two read-side callables the driver needs from a started resident server (BE-0332 Unit 3).

    `fetch` returns the current hierarchy and its read mark, blocking until the read postdates the mark
    it is passed (BE-0332 Unit 4); `clock` returns the device's current clock so the driver can anchor
    its read-lag barrier before a gesture; `act` performs a gesture on the device against an element the
    host already resolved, so no coordinate crosses; `type_text` enters text `input text` cannot carry
    into the focused field; `set_picker_value` walks a resolved `NumberPicker` to a named row (BE-0480),
    and is None only where a caller builds a channel without it. All five follow the lease's forwarded host port, which a restart moves;
    `recovery` is what a driver that fell back to `uiautomator dump` can ask of the server.
    """

    fetch: HierarchyFetch
    clock: ClockFetch
    act: ActFn
    type_text: TypeFn
    recovery: ChannelRecovery | None = None
    set_picker_value: PickerFn | None = None
