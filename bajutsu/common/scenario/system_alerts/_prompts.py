"""The label table behind every prompt the schema names."""

from __future__ import annotations

from typing import TypedDict

from ._shape import _Shape


class _Prompts(TypedDict):
    """Every prompt `SystemAlertPrompt` names, with its labels.

    Declaring a new prompt without its labels is then a type error, rather than a schema that
    accepts a step no lookup can resolve. The keys must stay in step with `SystemAlertPrompt`; the
    per-prompt maps are keyed by language subtag (lowercase, no region — see `system_alert_label`).
    """

    notifications: dict[str, list[_Shape]]
    tracking: dict[str, list[_Shape]]
    paste: dict[str, list[_Shape]]
    savePassword: dict[str, list[_Shape]]
    localNetwork: dict[str, list[_Shape]]
