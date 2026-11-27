"""One gesture's read-lag barrier: whether the tree has published the gesture yet."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.drivers.coordinate_tree import StableKey


@dataclass
class _Catchup:
    """One gesture's outstanding read-lag barrier: has the tree published the gesture yet?

    Android moves the content before it publishes the accessibility update naming the new frames, so a
    read taken in between describes the pre-gesture screen. `AdbDriver._advance_catchup` folds each read
    into this state and closes the barrier once the tree has demonstrably caught up.

    Two answers to "caught up?" live here, and `_advance_catchup` prefers the first available. When the
    resident channel stamps reads with a device event mark (BE-0332 Unit 3), a read caught up the moment
    its mark postdates `actuation_mark` — a genuine ordering test that releases as soon as the device
    publishes an update. On the `uiautomator dump` fallback, which carries no mark, `actuation_mark` is
    None and the barrier falls back to the projection-changed-and-dwelt heuristic (`pre_key`/`key`/
    `since`) bounded by `deadline`.

    The barrier gives up only once `deadline` has passed *and* `reads` reaches the driver's floor of
    completed reads, so a slow host spends more wall-clock rather than getting fewer chances (BE-0505).
    """

    pre_key: StableKey  # the projection the screen had when the gesture fired
    deadline: float  # wall-clock ceiling on waiting for the gesture to show up
    key: StableKey | None  # the newest non-degenerate projection seen since
    since: float  # when `key` was first seen — the dwell is measured from here
    actuation_mark: (
        float | None
    )  # the device-clock mark taken before the gesture (None on the dump path)
    armed_at: float  # when the gesture fired — how long the barrier took is measured from here, and
    # `since` cannot stand in for it because the dwell logic overwrites that
    reads: int = 0  # reads folded into this barrier since it was armed
