"""What a run keeps, how it compares, and what it masks, as written under `evidence:` (BE-0452)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from bajutsu.common.scenario import Redact

from ._model import _Model

DEFAULT_CAPTURE: tuple[str, ...] = ("screenshot.after", "elements", "actionLog")


class Evidence(_Model):
    """`evidence`: the artifacts a run captures, how it compares them, and the values it masks."""

    capture: list[str] = Field(default_factory=lambda: list(DEFAULT_CAPTURE))
    # Unions with `defaults.evidence.redact`, list by list, so a target never unmasks a team rule.
    redact: Redact = Field(default_factory=Redact)
    # Collect the app's network exchanges for `request` assertions; the `network` capture kind needs
    # it. The `--network` / `--no-network` flag wins over this value.
    collect_network: bool = Field(default=True, alias="collectNetwork")
    # How `visual` assertions compare; None falls through to the built-in `exact`.
    visual_compare: Literal["exact", "pixelmatch"] | None = Field(
        default=None, alias="visualCompare"
    )
    # An input injected as `${secrets.X}`, masked in evidence because of that role; the list unions
    # with `defaults.evidence.secrets`, so a target adding one never stops masking a team-wide one.
    secrets: list[str] = Field(default_factory=list)
