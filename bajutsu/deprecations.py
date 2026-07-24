"""One-time deprecation notices for renamed authoring / CLI surfaces.

A deprecated spelling (a scenario/config key, or a CLI flag) keeps working as an alias for its
canonical name; using one emits a single notice per process pointing at the new name. The notice is
an authoring / CLI-path log line only — never anything on the deterministic `run` verdict path
(prime directive 1) — and never changes a run's outcome, since the alias behaves identically to the
canonical name. Python's last-resort handler surfaces the WARNING to stderr when no logging is
configured, so the notice reaches a CLI user without any setup.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Codes already emitted this process, so a repeated use (many scenarios naming the same old key)
# warns once rather than on every occurrence.
_emitted: set[str] = set()


def warn_once(code: str, message: str) -> None:
    """Emit *message* at WARNING the first time *code* is seen this process; later calls no-op."""
    if code in _emitted:
        return
    _emitted.add(code)
    logger.warning(message)


def warn_deprecated_key(data: object, *, surface: str, old: str, new: str) -> None:
    """Warn once when a raw model input still carries the deprecated *old* key.

    Shared by the scenario and config `model_validator(mode="before")` hooks: the old key still parses
    via a Pydantic alias, but using it earns a one-time notice pointing at *new*. *surface* names the
    model ("scenario" / "config"), used both in the dedup code and the message ("<surface> field …").
    """
    if isinstance(data, dict) and old in data:
        warn_once(
            f"{surface}.{old}",
            f"{surface} field '{old}' is deprecated; rename it to '{new}' "
            f"('{old}' is still accepted for now).",
        )
