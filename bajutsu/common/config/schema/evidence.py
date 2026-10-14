"""What a run keeps and what it masks, as written under `evidence:` (BE-0452)."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario import Redact

from ._model import _Model

DEFAULT_CAPTURE: tuple[str, ...] = ("screenshot.after", "elements", "actionLog")


class Evidence(_Model):
    """`evidence`: the artifacts a run captures, and the values it masks in them."""

    capture: list[str] = Field(default_factory=lambda: list(DEFAULT_CAPTURE))
    # Unions with `defaults.evidence.redact`, list by list, so a target never unmasks a team rule.
    redact: Redact = Field(default_factory=Redact)
