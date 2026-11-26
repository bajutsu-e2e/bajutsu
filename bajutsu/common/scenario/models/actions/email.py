"""The `email` action: poll a mailbox until a matching message arrives (BE-0046)."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model

from .email_extract import EmailExtract
from .email_match import EmailMatch


class Email(_Model):
    """`email` — poll a mailbox until a matching message arrives, extract a value into `${vars.*}`.

    `match` selects the awaited message, `extract` pulls the value, and `timeout` (seconds, required)
    bounds the poll — a condition wait, never a fixed sleep (BE-0046). The mail server lives in
    config (`targets.<name>.services.mailbox`), so the scenario stays app-agnostic and credential-free.
    """

    match: EmailMatch
    extract: EmailExtract
    timeout: float = Field(gt=0)
