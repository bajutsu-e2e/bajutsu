"""Shared harness for the orchestrator test split: a logical clock, a scenario builder, and
the driver action-log reader the interrupt and guard tests assert on."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import Scenario


class FakeClock:
    """Advance logical time on sleep; `on_sleep` mutates the world over time."""

    def __init__(self, on_sleep: Callable[[float], None] | None = None) -> None:
        self._t = 0.0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self._t)


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def _tap_ids(driver: FakeDriver) -> list[object]:
    """The `id` of each tap the driver performed, in order.

    `FakeDriver.actions` logs `(kind, arg)` with `arg` typed `object`, so the read asserts the shape
    rather than casting it away: a tap logged as something other than a selector mapping fails the
    test loudly (BE-0388).
    """
    ids: list[object] = []
    for kind, arg in driver.actions:
        if kind != "tap":
            continue
        assert isinstance(arg, dict)
        ids.append(arg.get("id"))
    return ids
