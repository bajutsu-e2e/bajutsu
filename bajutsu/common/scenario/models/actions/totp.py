"""The `totp` action: generate an RFC 6238 one-time password into a variable (BE-0046)."""

from __future__ import annotations

from pydantic import Field, field_validator

from bajutsu.common.scenario.models._base import _Model

from .var_target import VarTarget

# The step fixes the RFC 6238 default period, so it is also the most `minRemaining` can ask for.
TOTP_PERIOD_SECONDS = 30


class Totp(_Model):
    """`totp` — generate an RFC 6238 time-based one-time password into `${vars.*}` (BE-0046).

    `secret` is the shared base32 key (commonly `${secrets.*}`); the current code is written to
    `into.var` for a later `type` / `assert` to consume. Local and deterministic — no LLM, no
    network, no scripting escape hatch. `minRemaining` (BE-0474), when set, makes the run loop wait
    for the next 30-second window first whenever the current one has fewer seconds left, so the code
    outlives the steps that carry it to the app.
    """

    secret: str
    into: VarTarget
    # Strict, so a YAML `true` or `"10"` is refused rather than coerced. The bounds are published for
    # schema-driven clients; the validator enforces them with an error that names the window length.
    min_remaining: int | None = Field(
        default=None,
        alias="minRemaining",
        strict=True,
        json_schema_extra={"minimum": 1, "maximum": TOTP_PERIOD_SECONDS},
    )

    @field_validator("min_remaining")
    @classmethod
    def _within_window(cls, v: int | None) -> int | None:
        if v is not None and not 1 <= v <= TOTP_PERIOD_SECONDS:
            raise ValueError(
                f"totp: minRemaining must be between 1 and {TOTP_PERIOD_SECONDS} — the seconds a "
                f"{TOTP_PERIOD_SECONDS}-second window must still have when the code is generated"
            )
        return v
