"""One command replacing the running app's stub table mid-scenario (BE-0365 unit 4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from bajutsu.common.scenario.models.mocks import Mock

from .in_app_capability import InAppCapability


class AppStubTableCommand(BaseModel):
    """One command replacing the running app's stub table mid-scenario (BE-0365 unit 4).

    The sibling `AppCommand`'s docstring asks for: a stub table is not a toggle, so it carries the
    whole table and no `enabled`. The table replaces the one BajutsuKit loaded from `BAJUTSU_MOCKS`
    rather than extending it, so the app's rules after the command are exactly `mocks` — an empty
    list removes every stub. Serialized with the same alias keys `dump_mocks` writes, so the app
    parses one wire shape whether the table arrived at launch or on this channel.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    capability: Literal[InAppCapability.STUB_TABLE] = InAppCapability.STUB_TABLE
    mocks: list[Mock]
