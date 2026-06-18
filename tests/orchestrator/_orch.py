"""Shared harness for the orchestrator test split: a logical clock and a scenario builder."""

from __future__ import annotations

from collections.abc import Callable

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
