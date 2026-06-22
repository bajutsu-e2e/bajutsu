"""Shared base for the scenario models: the strict pydantic base, small validators, and the
token grammars (capture kinds, step actions, assertion kinds). Everything here is dependency-free
so every model module can import it without cycles."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

Point = tuple[float, float]

# capture token grammar.
_CAPTURE_KINDS = {
    "screenshot",
    "elements",
    "actionLog",
    "deviceLog",
    "network",
    "video",
    "appTrace",
}
_CAPTURE_MODS = {"before", "after", "around", "onError"}

# The list of step-action field names is derived from the Step model itself (see
# models/steps.py), so adding an action edits exactly one place — the model — instead of also
# appending here. Only the control-flow subset, which the Step validator needs, is named.
_CONTROL_FLOW_ACTIONS = ("if_", "for_each")
_ASSERTION_KINDS = (
    "exists",
    "value",
    "label",
    "count",
    "enabled",
    "disabled",
    "selected",
    "request",
    "event",
    "visual",
)


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


def _exactly_one(obj: _Model, fields: tuple[str, ...], context: str) -> list[str]:
    """Return the list of set fields from *fields*; raise if not exactly one is set."""
    present = [f for f in fields if getattr(obj, f) is not None]
    if len(present) != 1:
        raise ValueError(f"exactly one of {list(fields)} required ({context}): {present or 'none'}")
    return present


def _validate_capture(tokens: list[str]) -> list[str]:
    for t in tokens:
        kind, _, mod = t.partition(".")
        if kind not in _CAPTURE_KINDS:
            raise ValueError(f"unknown capture kind: {t!r} (§9)")
        if mod and mod not in _CAPTURE_MODS:
            raise ValueError(f"unknown capture modifier: {t!r} (§9)")
    return tokens
