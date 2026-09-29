"""One command Bajutsu asks the running app to apply (BE-0365)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .in_app_capability import ToggleCapability


class AppCommand(BaseModel):
    """One command bajutsu asks the running app to apply (BE-0365).

    bajutsu-side only — the collector serializes these out and never parses one back, so this model
    is strict and frozen rather than forward-compatible like the reports the app POSTs. It carries
    no judgement: nothing here may influence whether a step passes, and no assertion reads it.

    `enabled` is the whole state a toggle capability takes. A capability whose state is not a toggle
    (the mid-scenario stub table, BE-0365 unit 4) arrives as the sibling `AppStubTableCommand`,
    discriminated on `capability`, not as another optional field here: widening this one would make the invalid cross-product — a stub table with
    no table, a toggle carrying one — representable, and leave a validator to rule out what a union
    rules out structurally (the shape `config/effective.py` already argues for).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    capability: ToggleCapability
    enabled: bool
