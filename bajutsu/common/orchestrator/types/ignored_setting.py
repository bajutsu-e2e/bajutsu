"""A setting the target's route cannot apply and the target declared ignored (BE-0467)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IgnoredSetting:
    """A setting a scenario put in effect that its target ran without, by declaration (BE-0467).

    Recorded per target, so two targets that ignore the same setting for different reasons both
    reach the report. Disclosed beside the verdict, never part of it.
    """

    target: str  # the target that declared it under `runsOn.ignoredSettings`
    key: str  # the setting key, e.g. "erase" or "permissions.camera"
    reason: str  # the declared reason the target runs without it
