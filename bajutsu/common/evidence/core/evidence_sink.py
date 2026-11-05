"""The seam every piece of evidence is written through during a run."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals

from .artifact import Artifact

if TYPE_CHECKING:
    # Imported for typing only — importing at runtime would cycle (orchestrator imports this module).
    # The writer reads these by attribute, so it needs no runtime import.
    from bajutsu.common.orchestrator.waits import WaitTrace


class EvidenceSink(Protocol):
    """Where evidence goes during a run.

    The orchestrator captures instant artifacts after each step, and records the
    interval artifacts (video / deviceLog / appTrace) for the whole scenario.
    """

    # `reuse_before_screenshot`, an already-written `"screenshot"`-kind `Artifact` (typically the
    # previous step's `after.png`), is a hint that its bytes may stand in for a fresh
    # `screenshot.before` capture (BE-0407 Unit 1). A conforming implementation is always free to
    # ignore it — `NullSink` does — and must fall back to a fresh capture for a `kind` other than
    # `"screenshot"` or `None`.
    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
        elements_source: str | None = None,
        reuse_before_screenshot: Artifact | None = None,
    ) -> list[Artifact]: ...

    def wait_diagnostic(
        self,
        step_id: str,
        *,
        trace: WaitTrace,
        elements: list[base.Element],
    ) -> Artifact | None: ...
    def start_scenario_intervals(
        self, scenario_id: str, kinds: list[str]
    ) -> list[intervals.Interval]: ...
    def finish_scenario_intervals(
        self, scenario_id: str, started: list[intervals.Interval]
    ) -> list[Artifact]: ...

    def add_secret_values(self, values: Sequence[str]) -> None:
        """Mask *values*, derived from a secret mid-scenario, in every artifact written next (BE-0472).

        Part of the protocol rather than an opt-in, so a new sink that forgets it fails the type
        check instead of silently writing the value in plain text.
        """
