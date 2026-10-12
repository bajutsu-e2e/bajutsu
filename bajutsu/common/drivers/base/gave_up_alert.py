"""A showing the backend stopped pressing (BE-0461)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GaveUpAlert:
    """A showing whose chosen button the backend pressed without clearing it, then stopped.

    Recorded once per showing, beside that showing's answer, which it never withdraws: the press
    happened, it just did not clear the prompt. The orchestrator turns it into the
    `uncleared_prompt_note` that names `tap`.

    Attributes:
        identify: The identifying labels of the rule that matched the showing.
        surface: Whether the showing was a SpringBoard alert (`native`) or a prompt in the
            application's own tree (`inTree`).
        tap: The button the rule chose.
    """

    identify: list[str]
    surface: Literal["native", "inTree"]
    tap: str
