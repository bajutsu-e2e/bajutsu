"""Pure mailbox logic for the `email` step (BE-0046, BE-0514).

`email` waits for a matching message that arrived *after* the step started and extracts a value by
regex into `vars.*`. This module holds the deterministic, network-free core — turning a raw RFC 5322
message into a normalized one, matching, extracting, and picking the newest — so it is fully
gate-tested; the IMAP / POP3 transport is injected at the handler layer (`runner/mailbox.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from bajutsu.common.scenario import EmailExtract, EmailMatch


@dataclass(frozen=True)
class MailboxMessage:
    """One inbox message, normalized from its raw form.

    `id` is the server's stable identity for the message — the after-start baseline keys on it.
    `to` is every address in the `To` header. `received_at` is the receive time in UTC, written in
    ISO 8601 form, or "" when the message has no usable date; it is only used to order newest-first,
    never compared to the local run clock (which would be skew-prone).
    """

    id: str
    to: tuple[str, ...]
    subject: str
    body: str
    received_at: str


class UnknownNewestError(ValueError):
    """Which of several matching messages is newest cannot be told from their receive times."""


def parse_message(raw: bytes, *, message_id: str, received: datetime | None) -> MailboxMessage:
    """Normalize one raw message. Pure; a malformed header or charset degrades, never raises.

    Args:
        raw: The message source as the server returned it.
        message_id: The server's identity for the message.
        received: When the server (or the message itself) says it arrived; None if unknown.
    """
    msg = message_from_bytes(raw, policy=policy.default)
    return MailboxMessage(
        id=message_id,
        to=_recipients(msg),
        subject=_header_text(msg, "Subject"),
        body=_safe_body(msg),
        received_at=_utc_text(received),
    )


def date_header(raw: bytes) -> datetime | None:
    """The time in a raw message's `Date` header, or None when it is absent or unreadable."""
    value = _header_text(message_from_bytes(raw, policy=policy.default), "Date")
    try:
        return parsedate_to_datetime(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


def _utc_text(when: datetime | None) -> str:
    if when is None:
        return ""
    # RFC 5322's "-0000" parses as a naive time that is already UTC.
    aware = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
    try:
        return aware.astimezone(UTC).isoformat(timespec="seconds")
    except (ValueError, OverflowError):
        return ""


def _header_text(msg: EmailMessage, name: str) -> str:
    # The stdlib's structured header parser raises on some malformed input; one bad message in the
    # inbox must not fail a step that is waiting for another.
    try:
        return str(msg.get(name, ""))
    except Exception:
        return ""


def _recipients(msg: EmailMessage) -> tuple[str, ...]:
    try:
        headers = msg.get_all("To", [])
        return tuple(a.addr_spec for header in headers for a in header.addresses)
    except Exception:  # same reason as `_header_text`
        return ()


def _text_parts(part: EmailMessage) -> Iterator[EmailMessage]:
    """The leaf parts that are not attachments.

    An embedded `message/*` is another mail, not this one's text, so it is not descended into.
    """
    if part.get_content_disposition() == "attachment":
        return
    if part.get_content_maintype() == "multipart":
        for child in part.iter_parts():
            yield from _text_parts(child)  # type: ignore[arg-type]  # default policy: EmailMessage
    elif not part.is_multipart():
        yield part


def _decode(part: EmailMessage) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    try:
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:  # an unknown charset name
        text = payload.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n")


def _body(msg: EmailMessage) -> str:
    parts = list(_text_parts(msg))
    chosen = [p for p in parts if p.get_content_type() == "text/plain"] or [
        p for p in parts if p.get_content_type() == "text/html"
    ]
    return "\n".join(_decode(p) for p in chosen)


def _safe_body(msg: EmailMessage) -> str:
    try:
        return _body(msg)
    except Exception:  # same reason as `_header_text`; an empty body then fails extraction cleanly
        return ""


def match_message(msg: MailboxMessage, match: EmailMatch) -> bool:
    """Whether a message satisfies the `match` criteria (to / subject / subjectMatches), AND-ed."""
    if match.to is not None and match.to.casefold() not in (a.casefold() for a in msg.to):
        return False
    if match.subject is not None and msg.subject != match.subject:
        return False
    return not (
        match.subject_matches is not None and re.search(match.subject_matches, msg.subject) is None
    )


def extract_value(body: str, extract: EmailExtract) -> str | None:
    """The value `extract.body_matches` pulls from a message body: first group, or the whole match.

    Returns None when the regex does not match (a matched message the step can't extract from — a
    clean step failure, not a silent wrong value).
    """
    m = re.search(extract.body_matches, body)
    if m is None:
        return None
    return m.group(1) if m.groups() else m.group(0)


def select(messages: list[MailboxMessage], match: EmailMatch) -> MailboxMessage | None:
    """The awaited message among `messages`: the one matching message with the newest time. Pure.

    The caller passes only messages that arrived after the step started.

    Raises:
        UnknownNewestError: Two or more messages match and the newest cannot be told, because one
            has no date or the newest time is shared (receive times have one-second resolution). It
            never falls back to the identifier, which could pick a superseded code.
    """
    eligible = [m for m in messages if match_message(m, match)]
    if len(eligible) < 2:
        return eligible[0] if eligible else None
    newest = max(eligible, key=lambda m: m.received_at)
    if any(not m.received_at for m in eligible):
        why = "at least one has no date"
    elif sum(m.received_at == newest.received_at for m in eligible) > 1:
        why = "the newest ones share a receive time"
    else:
        return newest
    raise UnknownNewestError(
        f"{len(eligible)} messages match and {why}, so the newest is unknown; narrow `match`"
    )
