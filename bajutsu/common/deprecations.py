"""One-time notices for renamed authoring / CLI surfaces, and errors for removed ones.

Two halves of the same rename lifecycle, plus the process-wide dedup both rest on. `warn_once` emits
a message the first time its code is seen, so a notice that would otherwise repeat per scenario (a
deprecated spelling, or a config-level rule reaching a scenario that answers for itself, BE-0401)
reaches the operator once. `reject_renamed_key` is the other end: a spelling that was removed rather
than aliased fails to load naming its replacement, instead of leaving Pydantic's generic
extra-field error.

Everything here is an authoring / CLI-path line only — never anything on the deterministic `run`
verdict path (prime directive 1). Python's last-resort handler surfaces a WARNING to stderr when no
logging is configured, so a notice reaches a CLI user without any setup.
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


def reject_renamed_key(data: object, *, surface: str, old: str, new: str) -> None:
    """Raise when a raw model input still carries the removed *old* key, naming *new*.

    Shared by the scenario and config `model_validator(mode="before")` hooks for a spelling deleted
    with no alias (BE-0401). *surface* names the model ("scenario" / "config"), so the message says
    which file the author has to edit.

    Raises:
        ValueError: *data* carries *old*.
    """
    if isinstance(data, dict) and old in data:
        raise ValueError(f"{surface} field '{old}' was removed; rename it to '{new}'")
