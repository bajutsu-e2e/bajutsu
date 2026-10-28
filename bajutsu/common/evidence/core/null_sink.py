"""The default sink, capturing nothing, so a run stays side-effect free unless asked."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from bajutsu.common.drivers import base
from bajutsu.common.evidence import intervals

from .artifact import Artifact

if TYPE_CHECKING:
    from bajutsu.common.orchestrator.waits import WaitTrace


class NullSink:
    """Default sink: capture nothing (keeps runs side-effect free unless asked)."""

    def capture(
        self,
        driver: base.Driver,  # noqa: ARG002  # EvidenceSink shape
        step_id: str,  # noqa: ARG002
        kinds: list[str],  # noqa: ARG002
        *,
        elements: list[base.Element] | None = None,  # noqa: ARG002
        elements_source: str | None = None,  # noqa: ARG002
        reuse_before_screenshot: Artifact | None = None,  # noqa: ARG002
    ) -> list[Artifact]:
        return []

    def wait_diagnostic(
        self,
        step_id: str,  # noqa: ARG002  # EvidenceSink shape
        *,
        trace: WaitTrace,  # noqa: ARG002
        elements: list[base.Element],  # noqa: ARG002
    ) -> Artifact | None:
        return None

    def start_scenario_intervals(
        self,
        scenario_id: str,  # noqa: ARG002  # EvidenceSink shape
        kinds: list[str],  # noqa: ARG002
    ) -> list[intervals.Interval]:
        return []

    def finish_scenario_intervals(
        self,
        scenario_id: str,  # noqa: ARG002  # EvidenceSink shape
        started: list[intervals.Interval],  # noqa: ARG002
    ) -> list[Artifact]:
        return []

    def add_secret_values(self, values: Sequence[str]) -> None:  # noqa: ARG002  # EvidenceSink shape
        return None  # nothing is written, so nothing needs masking
