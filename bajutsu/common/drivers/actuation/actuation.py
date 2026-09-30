"""One primitive a driver performed on the device, as the `actionLog` evidence records it (BE-0345)."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers.base import Frame, Point


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
        at: The instant the driver handed this primitive to its transport. A monotonic instant while
            the record sits in the driver's `ActuationLog`, and absolute epoch seconds — the same
            footing as `StepOutcome.started_at` — once the runner drains it into a step. A report
            switches the highlighted step row at this instant rather than at the step's start,
            because the recording only changes once the action lands, which on a backend that reads
            the tree first is seconds after the step began. `None` on every run recorded before it
            existed.
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
    at: float | None = None
