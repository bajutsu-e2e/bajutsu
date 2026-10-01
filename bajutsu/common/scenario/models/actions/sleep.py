"""The `sleep` action: pause the scenario for a fixed, stated, capped number of seconds."""

from __future__ import annotations

from pydantic import Field, field_validator

from bajutsu.common.scenario.models._base import _Model

# A fixed pause longer than this is almost certainly a condition nobody wrote down, so the loader
# refuses it and points at `wait` instead.
MAX_SLEEP_SECONDS = 30.0


class Sleep(_Model):
    """`sleep` action — pause for `seconds`, the one sanctioned exception to "condition waits only".

    For a wait no condition on the screen, the tree, or the network log can observe (a server-side
    throttle, an animation that outlives `until: settled`). `reason` is mandatory so every fixed
    pause says why a `wait` could not express it; the report shows it and the determinism audit
    lists it as a `fixed-sleep` finding.
    """

    # Strict, so a YAML `true` or `"2"` is refused rather than coerced past the cap's intent.
    # `maximum` is published for schema-driven clients; the validator below enforces it with an
    # error that points at `wait`, which a plain `le=` constraint would replace with a generic one.
    seconds: float = Field(gt=0, strict=True, json_schema_extra={"maximum": MAX_SLEEP_SECONDS})
    reason: str

    @field_validator("seconds")
    @classmethod
    def _capped(cls, v: float) -> float:
        if v > MAX_SLEEP_SECONDS:
            raise ValueError(
                f"sleep: seconds must be at most {MAX_SLEEP_SECONDS:g} — a longer fixed pause is "
                "almost always a missing condition; use `wait` with `for` / `until` instead"
            )
        return v

    @field_validator("reason")
    @classmethod
    def _stated(cls, v: str) -> str:
        if not v.strip():
            raise ValueError(
                "sleep: reason must say why no `wait` condition can express this pause"
            )
        return v
