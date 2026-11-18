"""The `totp` action: generate an RFC 6238 one-time password into a variable (BE-0046)."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.totp import TOTP_PERIOD_SECONDS

from .var_target import VarTarget


def min_remaining_field() -> Any:
    """The `minRemaining` field `totp` and `otp` share: the seconds the current window must still have.

    Strict, so a YAML `true` or `"10"` is refused rather than coerced. The step fixes the window at
    the generator's default period, which is therefore the most it can ask for. The bounds are
    published for schema-driven clients; `check_min_remaining` enforces them with an error that
    names the window length.
    """
    return Field(
        default=None,
        alias="minRemaining",
        strict=True,
        json_schema_extra={"minimum": 1, "maximum": TOTP_PERIOD_SECONDS},
    )


def check_min_remaining(kind: str, v: int | None) -> int | None:
    """Refuse a `minRemaining` outside one window, naming the step `kind` it was written on."""
    if v is not None and not 1 <= v <= TOTP_PERIOD_SECONDS:
        raise ValueError(
            f"{kind}: minRemaining must be between 1 and {TOTP_PERIOD_SECONDS} — the seconds a "
            f"{TOTP_PERIOD_SECONDS}-second window must still have when the code is generated"
        )
    return v


class Totp(_Model):
    """`totp` — generate an RFC 6238 time-based one-time password into `${vars.*}` (BE-0046).

    `secret` is the shared base32 key (commonly `${secrets.*}`); the current code is written to
    `into.var` for a later `type` / `assert` to consume. `name`, when set, also registers the secret
    as a named generator, so a later `otp` step can fetch a fresh code without restating it — which
    is why `into` may be omitted when `name` is given. Local and deterministic — no LLM, no network,
    no scripting escape hatch. `minRemaining` (BE-0474), when set, makes the run loop wait for the
    next 30-second window first whenever the current one has fewer seconds left, so the code
    outlives the steps that carry it to the app.
    """

    secret: str
    into: VarTarget | None = None
    name: str | None = Field(default=None, min_length=1)
    min_remaining: int | None = min_remaining_field()

    @field_validator("min_remaining")
    @classmethod
    def _within_window(cls, v: int | None) -> int | None:
        return check_min_remaining("totp", v)

    @model_validator(mode="after")
    def _produces_something(self) -> Self:
        if self.into is None and self.name is None:
            raise ValueError(
                "totp: give `into` (write a code), `name` (register a generator), or both"
            )
        if self.into is None and self.min_remaining is not None:
            # The wait keeps a produced code fresh; a register-only step produces none.
            raise ValueError(
                "totp: minRemaining needs `into` — a step with no code has nothing to wait for"
            )
        return self
