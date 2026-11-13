"""The `clipboard` assertion: what the app put on the pasteboard."""

from __future__ import annotations

from typing import Self

from pydantic import model_validator

from bajutsu.common.scenario.models._base import _Model


class ClipboardMatch(_Model):
    """`clipboard`: verify what the app copied to the pasteboard — exactly one of equals / matches.

    Read off the device (`simctl pbpaste` on iOS; the BajutsuAndroid receiver on Android, BE-0233),
    so it needs the per-device control channel and is
    unavailable on the fake driver / in parallel runs.
    """

    equals: str | None = None
    matches: str | None = None  # regex over the clipboard text

    @model_validator(mode="after")
    def _one_op(self) -> Self:
        if sum(o is not None for o in (self.equals, self.matches)) != 1:
            raise ValueError("clipboard requires exactly one of equals/matches (§6.4)")
        return self
