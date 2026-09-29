"""The inbound half of a collector: the calls a mid-scenario command needs (BE-0365)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .app_command_report import AppCommandReport
from .in_app_capability import ToggleCapability

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.mocks import Mock


@runtime_checkable
class ControlChannel(Protocol):
    """The inbound half of a collector: the calls a mid-scenario command needs (BE-0365).

    Deliberately a sibling of `Collector` rather than more methods on it. Which collectors carry a
    channel is the question unit 3 has to answer, and answering it structurally means a collector
    that has no way to reach the app simply does not satisfy this — no stub method that raises, and
    nothing for `WebNetworkCollector` or a test fake to implement. `NetworkCollector` satisfies it
    because its receiver already holds the queue, and the driver-observed collectors do not — but
    that split is narrower than "can this backend carry a channel?": Android is an
    external-receiver platform too (BE-0283) — as is `fake` — so those leases hold a
    `NetworkCollector` and satisfy this even though nothing on the other end drains the queue.
    `orchestrator.control_channel` turns a command issued against a channel-less collector into a
    loud failure rather than a silent skip; against an Android one it can only time out, so keeping
    the command from being issued there is the caller's job, not this protocol's.
    """

    def enqueue_command(self, capability: ToggleCapability, *, enabled: bool) -> str: ...
    def enqueue_stub_table(self, mocks: Sequence[Mock]) -> str: ...
    def report_for(self, command_id: str) -> AppCommandReport | None: ...
