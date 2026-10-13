"""The LogBus seam: how a job's live log reaches a subscriber (BE-0015 local/server parity).

A run/record/crawl job streams output line by line. `LogBus` is the one point where delivery
varies: a worker buffers its own job's lines in process (`InMemoryLogBus`); the server uses
`QueueLogBus` (BE-0449), which
relays the lines a worker posts while the job runs (through an in-memory or Redis bus) and falls
back to the log uploaded to object storage once it completes. A subscriber gets the backlog already
published plus any live lines, and the stream ends once the job is `close`d — so a subscriber
that attaches after the job finished still replays everything.
"""

from ._channel import _Channel as _Channel
from .in_memory_log_bus import InMemoryLogBus
from .live_line_budget import LiveLineBudget
from .log_bus import ARTIFACT_LINE_PREFIX, LogBus

__all__ = ["ARTIFACT_LINE_PREFIX", "InMemoryLogBus", "LiveLineBudget", "LogBus"]
