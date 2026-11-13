"""The `otp` action: fetch the current code from a generator a `totp` step named."""

from __future__ import annotations

from pydantic import Field, field_validator

from bajutsu.common.scenario.models._base import _Model

from .totp import check_min_remaining, min_remaining_field
from .var_target import VarTarget


class Otp(_Model):
    """`otp` — write the current code of a named TOTP generator into `${vars.*}`.

    `from` names a generator an earlier `totp` step registered with `name`; the step reads the clock
    when it runs, so a scenario fetches a fresh code at any point without restating the secret.
    Registration follows execution order: an `otp` whose generator has not been registered yet
    fails the step. `minRemaining` is the same window wait `totp` offers (BE-0474).
    """

    # `from_` for the same reason `Step.from_` is: `from` is a Python keyword.
    from_: str = Field(alias="from", min_length=1)
    into: VarTarget
    min_remaining: int | None = min_remaining_field()

    @field_validator("min_remaining")
    @classmethod
    def _within_window(cls, v: int | None) -> int | None:
        return check_min_remaining("otp", v)
