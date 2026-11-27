"""Publish a `run-set` group's lifecycle to operator-defined sinks (BE-0500).

A group records three events (`queued`, `started`, `completed`) in the store transaction that
causes each; a loop on `serve` delivers them, after the verdict, through one adapter per sink format.
"""

from .delivery import deliver_once, start_delivery_loop
from .group_sink import (
    GITHUB_CHECK,
    KNOWN_FORMATS,
    GroupNotifyConfigError,
    GroupSink,
    load_group_sinks,
)
from .snapshot import group_snapshot

__all__ = [
    "GITHUB_CHECK",
    "KNOWN_FORMATS",
    "GroupNotifyConfigError",
    "GroupSink",
    "deliver_once",
    "group_snapshot",
    "load_group_sinks",
    "start_delivery_loop",
]
