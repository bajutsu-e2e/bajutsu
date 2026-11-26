"""The mailbox an `email` step reads, as written under `mailbox:` (BE-0046, BE-0186, BE-0514)."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from ._model import _Model

# The HTTP mailbox's keys, removed with it by BE-0514. The error is the whole migration path an
# author gets, so it names every replacement.
_REMOVED_KEYS = ("url", "headers", "messages", "fields")


class Mailbox(_Model):
    """The mail server an `email` step reads (`targets.<name>.services.mailbox`).

    `kind` selects the adapter (`imap` or `pop3`) from the mailbox registry; an unknown `kind` fails
    closed when the runner resolves the mailbox, not here (the deterministic config must not import
    the registry, BE-0112). `host`, `username`, `password`, and `folder` may be `${secrets.*}`.
    `port` defaults from `kind` and `security`. Both `security` modes use TLS with certificate and
    server-name verification; there is no clear-text mode. `folder` applies to IMAP alone (default
    `INBOX`), because POP3 has a single inbox.
    """

    kind: str
    host: str
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["tls", "starttls"] = "tls"
    username: str
    password: str
    folder: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _removed_http_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        present = [key for key in _REMOVED_KEYS if key in data]
        if present:
            raise ValueError(
                f"mailbox no longer reads over HTTP; remove {', '.join(present)} (BE-0514). "
                "Describe an IMAP or POP3 server (`kind: imap` or `kind: pop3`), "
                "or read an HTTP mailbox with the `http` step"
            )
        return data

    @model_validator(mode="after")
    def _folder_is_imap_only(self) -> Self:
        if self.kind == "pop3" and self.folder is not None:
            raise ValueError("mailbox.folder applies to imap only; POP3 has a single inbox")
        return self
