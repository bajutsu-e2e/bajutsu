"""The run policy every platform shares, as written under a target's `run:` (BE-0452)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from bajutsu.common.scenario import SystemAlertHandlingField

from ._model import _Model

# Where each field BE-0473 moved out of `run` now lives. Pydantic's generic extra-field error names
# no replacement, and the loader reads none of the old keys, so the error is the migration path.
MOVED_RUN_KEYS: dict[str, str] = {
    "network": "evidence.collectNetwork",
    "visualCompare": "evidence.visualCompare",
    "secrets": "evidence.secrets",
}


class RunPolicy(_Model):
    """`run`: the policy of a run, the fields every platform shares.

    A platform adds its own fields through the registry (iOS's `tipKitHandling`); the target
    validates the two together, so an unknown key lists both sets. Each value sits between the
    scenario's own value and the built-in default (BE-0177): None falls through to the built-in.
    """

    erase: bool | None = None
    system_alert_handling: SystemAlertHandlingField = Field(
        default=None, alias="systemAlertHandling"
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_moved_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for old, new in MOVED_RUN_KEYS.items():
                if old in data:
                    raise ValueError(f"run.{old} moved to {new} (BE-0473)")
        return data
