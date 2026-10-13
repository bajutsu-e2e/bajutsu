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


# The server-side Device Farm budget settings, deprecated for one release once the worker owns the
# budget (BE-0448): still honored as the server-side cap meanwhile, then removed with the target-config
# restructure's schema. One code each, so every surface still in use is named once.
SERVER_BATCH_BUDGET_REPLACEMENT = (
    "set maxJobConcurrency in the Device Farm worker's worker.yaml instead; it is still honored as "
    "a server-side cap for this release, and will be removed"
)


def warn_server_batch_budget(surface: str) -> None:
    """Warn once that *surface*, a server-side Device Farm budget setting, is deprecated (BE-0448)."""
    warn_once(
        f"serverBatchBudget.{surface}",
        f"{surface} is deprecated (BE-0448): {SERVER_BATCH_BUDGET_REPLACEMENT}",
    )


# The hand-written routing-token inputs, deprecated once the worker capability supplies what they
# were for (BE-0450). The worker's override goes in the next release; `requires` waits for the item
# that derives a runtime or device-class requirement from the target's `device`, since removing it
# first would leave an `ios18` or `ipad` job no way to reach a compatible worker.
WORKER_CAPABILITIES_REPLACEMENT = (
    "the runtime and device-class tokens now come from the worker's Simulator inventory, and its "
    "environment and target limit from worker.yaml (--worker-config); it is still honored for this "
    "release and will be removed in the next"
)

REQUIRES_REPLACEMENT = (
    "a job now requires its platform, plus the host operating system its driver runs on; "
    "requiring an iOS runtime or a device class is not supported until that requirement is derived "
    "from the target's `device`. It is still honored until then. This notice comes at config load, "
    "so a local `bajutsu run` shows it too, although `requires` routes server jobs alone"
)


def warn_worker_capabilities(surface: str) -> None:
    """Warn once that *surface*, a worker's hand-written capability override, is deprecated (BE-0450)."""
    warn_once(
        f"workerCapabilities.{surface}",
        f"{surface} is deprecated (BE-0450): {WORKER_CAPABILITIES_REPLACEMENT}",
    )


def warn_requires(surface: str) -> None:
    """Warn once that *surface*, a configuration's `requires` list, is deprecated (BE-0450)."""
    warn_once(f"requires.{surface}", f"{surface} is deprecated (BE-0450): {REQUIRES_REPLACEMENT}")
