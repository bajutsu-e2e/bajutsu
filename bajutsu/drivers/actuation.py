"""What a driver actually did to the screen — the record behind the `actionLog` evidence kind.

A step's outcome has always named its action and its duration, never the concrete gesture: which
coordinate a tap injected, which two endpoints a swipe travelled, which channel carried it. Those
values exist only inside a driver, for the moment between resolving them and sending them, so the
driver is the only layer that can record them.

Three rules shape the record, and every backend honors them:

1. It holds the coordinate that was really handed to the platform, and never one reconstructed
   afterwards. A driver that computes a frame center and sends it records that center; a handle-based
   iOS tap leaves `points` empty, because XCUITest picked the point on the far side of the handle and
   writing the frame's center here would present a guess as a measurement.
2. It costs no device work. Every value is one the actuator already had for its own use, so recording
   adds no query, no read, and no round trip.
3. It carries no string a scenario authored, because `manifest.json` is written without a redactor: a
   `type` step's text, a `selectOption`'s option, and an element's accessibility label can each hold a
   resolved `${secrets.*}`. `target` is therefore always the resolved `Element["identifier"]`, and a
   typed string leaves not even its length behind (`evidence/redaction.py` replaces a secret with a
   fixed-width placeholder precisely so no artifact discloses one's length).

The record is evidence only. Nothing on the verdict path reads it — no assertion, wait, or extract —
so it cannot influence pass/fail (prime directive 1).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from bajutsu.drivers.base import Frame, Point

logger = logging.getLogger("bajutsu.actuation")

# The driver primitives a record may name. `str` rather than a Literal on purpose: the report
# reconstructs these records from a manifest an older or newer version of the tool wrote (BE-0068
# makes that compatibility the loader's contract), so a Literal would assert at the type level a
# guarantee the file on disk cannot make. A reader treats an unlisted value as opaque text.
GESTURES: tuple[str, ...] = (
    "tap",
    "doubleTap",
    "longPress",
    "swipe",
    "scroll",
    "pinch",
    "rotate",
    "typeText",
    "deleteText",
    "selectAll",
    "copy",
    "selectOption",
    "systemAlert",
    "back",
)

# How a gesture reached its target. `coordinate` is the only value that implies `points`.
CHANNELS: tuple[str, ...] = (
    "coordinate",  # the driver computed (or was handed) a point and sent it
    "handle",  # XCUITest actuated a snapshot handle; it chose the point
    "identity",  # the Android device resolved the element and chose the point
    "focused",  # a text primitive on whatever field holds focus, addressing no element
    "key",  # a key event (Android's system back), no coordinate at all
    "history",  # browser history navigation
)

# The coordinate space a record's numbers live in. iOS reports points, Android raw pixels, a browser
# (and a WebView's own space) CSS pixels — so a coordinate is only comparable alongside its space.
UNITS: tuple[str, ...] = ("point", "pixel", "cssPixel")

# The accumulator's cap, sized well above the worst case for one drained step: a `scroll` spends up to
# `maxScrolls` gestures (default 15, author-settable with no ceiling) and an Android `tap` can add
# three more swipes bringing its target on screen. It exists for the consumers that never drain — the
# crawl, `record`'s replay, the conformance suite — which would otherwise accumulate one record per
# gesture for a whole session.
MAX_RECORDS = 512


@dataclass(frozen=True)
class Actuation:
    """One primitive a driver performed on the device.

    Args:
        gesture: Which primitive ran, from `GESTURES`.
        via: How it reached its target, from `CHANNELS`.
        unit: The coordinate space `points` and `frame` are in, from `UNITS`. Always set, even for a
            record carrying neither, so every backend's records read uniformly.
        points: The contact points touched, in order — one for a tap, two for a drag's start and end.
            Empty whenever no coordinate crossed to the platform (rule 1 above).
        frame: The resolved element's bounds, where the driver resolved an element.
        target: The resolved element's accessibility identifier, and nothing else — never a label or a
            backend's richer addressing value, so the field cannot carry authored text (rule 3).
        duration_s: How long a press or drag was held.
        scale: A pinch's spread factor.
        radians: A rotation's angle.
    """

    gesture: str
    via: str
    unit: str
    points: list[Point] = field(default_factory=list)
    frame: Frame | None = None
    target: str | None = None
    duration_s: float | None = None
    scale: float | None = None
    radians: float | None = None


class ActuationLog:
    """The actuations a driver has performed since the last drain.

    Bounded (see `MAX_RECORDS`) so an undraining consumer keeps the most recent records instead of
    growing with the session. Dropping is counted and warned about rather than silent: the earliest
    gestures of a step are exactly what "the scroll never reached its target" needs to show.
    """

    def __init__(self, maxlen: int = MAX_RECORDS) -> None:
        self._records: deque[Actuation] = deque(maxlen=maxlen)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """How many records the cap has discarded over this log's lifetime."""
        return self._dropped

    def record(self, actuation: Actuation) -> None:
        """Append one actuation, discarding the oldest if the log is already full."""
        if len(self._records) == self._records.maxlen:
            self._dropped += 1
            if self._dropped == 1:
                # Once per log: a pathological step (or an undraining consumer) would otherwise
                # repeat this line per gesture, and one line already says the record is truncated.
                logger.warning(
                    "actuation log full at %d records; dropping the oldest — the earliest gestures "
                    "of this step are no longer in the record",
                    self._records.maxlen,
                )
        self._records.append(actuation)

    def drain(self) -> list[Actuation]:
        """Everything recorded since the last drain, oldest first, emptying the log."""
        out = list(self._records)
        self._records.clear()
        return out


@runtime_checkable
class ActuationReporter(Protocol):
    """A backend that reports the concrete actuations it performed.

    A narrow opt-in, like `ViewportProvider` / `ReadLagProvider` / `SettledReadProvider` in
    `bajutsu/drivers/base.py`: a backend that does not implement it simply contributes no records and
    the run is otherwise unchanged. The orchestrator drains once per step, so each step's outcome
    carries exactly the actuations that step performed.
    """

    def drain_actuations(self) -> list[Actuation]: ...
