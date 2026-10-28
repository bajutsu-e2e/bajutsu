"""The run policy every platform shares, as written under a target's `run:` (BE-0452)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from bajutsu.common.capability.settings_support import SETTING_KEYS
from bajutsu.common.scenario import SystemAlertHandlingField

from ._model import _Model


class RunPolicy(_Model):
    """`run`: the policy of a run, the fields every platform shares.

    A platform adds its own fields through the registry (iOS's `tipKitHandling`); the target
    validates the two together, so an unknown key lists both sets. Each value sits between the
    scenario's own value and the built-in default (BE-0177): None falls through to the built-in.
    """

    erase: bool | None = None
    network: bool | None = None
    visual_compare: Literal["exact", "pixelmatch"] | None = Field(
        default=None, alias="visualCompare"
    )
    # An input injected as `${secrets.X}`, masked in evidence because of that role; the list unions
    # with `defaults.run.secrets`, so a target adding one never stops masking a team-wide one.
    secrets: list[str] = Field(default_factory=list)
    system_alert_handling: SystemAlertHandlingField = Field(
        default=None, alias="systemAlertHandling"
    )
    # Environment-specific settings this target's route cannot apply and runs without, each with the
    # reason the report shows (BE-0467). Target-only: `Defaults` refuses it, since an ignored setting
    # belongs to one environment.
    ignored_settings: dict[str, str] = Field(default_factory=dict, alias="ignoredSettings")

    @field_validator("ignored_settings")
    @classmethod
    def _known_settings(cls, v: dict[str, str]) -> dict[str, str]:
        if unknown := [k for k in v if k not in SETTING_KEYS]:
            raise ValueError(
                f"run.ignoredSettings: unknown setting(s) {', '.join(map(repr, unknown))}; "
                f"known settings: {', '.join(SETTING_KEYS)}"
            )
        if blank := [k for k, reason in v.items() if not reason.strip()]:
            raise ValueError(
                f"run.ignoredSettings: {', '.join(map(repr, blank))} needs a non-blank reason"
            )
        return v
