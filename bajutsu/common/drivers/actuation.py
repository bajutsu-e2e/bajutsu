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

from collections import deque
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from bajutsu.common.drivers.base import Frame, Point

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
    "setPickerValue",
    "systemAlert",
    "back",
)

# How a gesture reached its target. `coordinate` is the only value that implies `points`.
CHANNELS: tuple[str, ...] = (
    "coordinate",  # the driver computed (or was handed) a point and sent it
    "handle",  # XCUITest actuated a snapshot handle; it chose the point
    "identity",  # the Android device resolved the element and chose the point
    "bridge",  # a WebView bridge call addressed by element id, which picks its own coordinate
    "focused",  # a text primitive on whatever field holds focus, addressing no element
    "key",  # a key event (Android's system back), no coordinate at all
    "history",  # browser history navigation
)

# Why the element actuated is not the one the driver's default rule would have named. Absent on the
# ordinary path. This is a separate axis from `via`, which answers how the gesture reached its target:
# a substituted tap still travels by `handle`; what changed is *which* element. Like `GESTURES`, an
# unlisted value is opaque text to a reader rather than an error, so an older report stays loadable.
SUBSTITUTIONS: tuple[str, ...] = (
    # The tap resolved uniquely but was refused, and exactly one named descendant inside its frame
    # was reachable — a container inflated over the control it wraps (BE-XXXX).
    "soleHittableDescendant",
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


@dataclass(frozen=True, kw_only=True)
class Actuation:
    """One primitive a driver performed on the device.

    Keyword-only by construction: `gesture`, `via`, and `unit` are three adjacent `str`s that no type
    checker could tell apart positionally, so naming them at every call site is the one invariant here
    that the type can enforce rather than leave to prose.

    Args:
        gesture: Which primitive ran, from `GESTURES`.
        via: How it reached its target, from `CHANNELS`.
        unit: The coordinate space `points` and `frame` are in, from `UNITS`. Always set, even for a
            record carrying neither, so every backend's records read uniformly.
        points: The coordinates the driver sent, in order — one for a tap, two for a drag's start and
            end. Empty whenever no coordinate crossed to the platform (rule 1 above). A two-finger
            gesture records the single anchor it derived its contacts from, not the contacts
            themselves: the anchor plus `frame` and `scale`/`radians` determine both fingers, while the
            contacts alone would read as two independent touches.
        frame: The resolved element's bounds, where the driver resolved an element.
        target: The resolved element's accessibility identifier, and nothing else — never a label or a
            backend's richer addressing value, so the field cannot carry authored text (rule 3).
        accepted: Whether the platform accepted this attempt, on the two channels that answer —
            XCUITest's handle actuation and Android's device-side endpoint, both of which can refuse
            and be retried. `None` means the driver got no separate answer (a fire-and-forget
            injection, or Android's "the request went out but the reply was lost"), in which case the
            step's own `ok` / `reason` is what says whether the step worked.
        duration_s: How long a press or drag was held.
        scale: A pinch's spread factor.
        radians: A rotation's angle.
        substitution: Why the element actuated is not the one the driver's default rule would have
            named, from `SUBSTITUTIONS`; absent on the ordinary path. Rule 3 holds: a fixed token,
            never a string a scenario authored.
    """

    gesture: str
    via: str
    unit: str
    points: tuple[Point, ...] = ()
    frame: Frame | None = None
    target: str | None = None
    accepted: bool | None = None
    duration_s: float | None = None
    scale: float | None = None
    radians: float | None = None
    substitution: str | None = None


@dataclass(frozen=True)
class Drained:
    """One drain's worth of records, plus what the cap discarded to make room for them.

    `dropped` travels with the records rather than staying a log-side counter so a truncated record
    can be *disclosed as truncated* wherever it is shown. A warning line would not do: this item's own
    reasoning for existing is that a log line is not evidence — absent unless someone raised the level
    before the run, and it never reaches the run directory.
    """

    records: list[Actuation]
    dropped: int


class ActuationLog:
    """The actuations a driver has performed since the last drain.

    Bounded (see `MAX_RECORDS`) so an undraining consumer keeps the most recent records instead of
    growing with the session. Dropping is counted, not silent: the earliest gestures of a step are
    exactly what "the scroll never reached its target" needs to show.
    """

    def __init__(self, maxlen: int = MAX_RECORDS) -> None:
        self._records: deque[Actuation] = deque(maxlen=maxlen)
        self._dropped = 0

    def record(self, actuation: Actuation) -> None:
        """Append one actuation, discarding the oldest if the log is already full."""
        if len(self._records) == self._records.maxlen:
            self._dropped += 1
        self._records.append(actuation)

    def settle(self, accepted: bool) -> None:
        """Stamp the most recent record with the answer the platform just gave.

        A record is written before its transport answers, so a gesture that failed still shows what it
        aimed at. On the two channels that *can* refuse and be retried, this is how a refused attempt
        stops reading as one that landed — without it, a stale-retried tap leaves three identical
        records and nothing saying which one the device honored. A no-op on an empty log, so a driver
        that settles without having recorded cannot corrupt the previous step's last record: the drain
        already took it.
        """
        if self._records:
            self._records[-1] = replace(self._records[-1], accepted=accepted)

    def drain(self) -> Drained:
        """Everything recorded since the last drain, oldest first, emptying the log."""
        out = Drained(records=list(self._records), dropped=self._dropped)
        self._records.clear()
        self._dropped = 0
        return out


@runtime_checkable
class ActuationReporter(Protocol):
    """A backend that reports the concrete actuations it performed.

    A narrow opt-in, like `ViewportProvider` / `ReadLagProvider` / `SettledReadProvider` in
    `bajutsu/common/drivers/base.py`: a backend that does not implement it simply contributes no records and
    the run is otherwise unchanged. The orchestrator drains once per step, so each step's outcome
    carries exactly the actuations that step performed.
    """

    def drain_actuations(self) -> Drained: ...
