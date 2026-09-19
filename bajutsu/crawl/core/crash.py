"""A path whose last action collapsed the app's UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .action import Action


@dataclass(frozen=True)
class Crash:
    """A path whose last action collapsed the app UI.

    `path` holds the human-readable action descriptions (for the report); `actions` the structured,
    replayable sequence the same path is built from, so a deterministic repro scenario can be
    emitted from it (BE-0038). `actions` is empty for a map saved before crashes carried it.
    """

    path: tuple[str, ...]
    actions: tuple[Action, ...] = ()
    # The platform's own report for this crash — the `.ips` macOS wrote, Android's `logcat` crash
    # block (BE-0424). Captured only when the driver positively confirmed the event, so a UI-tree
    # false positive records a `Crash` with no artifacts rather than paying a full-timeout sweep.
    #
    # In-memory only, and deliberately left out of `serialize.py`'s round trip in both directions:
    # raw `bytes` has no JSON encoding, and base64-widening every other field's dump to carry it
    # would cost more than it buys. Nothing durably persists it before `write_repros` writes it to
    # disk at the end of a completed crawl, so a `--resume` loses nothing it could have kept.
    artifacts: tuple[tuple[str, bytes], ...] = ()
