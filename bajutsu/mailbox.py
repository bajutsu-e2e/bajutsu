"""Pure mailbox logic for the `email` step (BE-0046).

`email` polls a generic HTTP mailbox, waits for a matching message that arrived *after* the step
started, and extracts a value by regex into `vars.*`. This module holds the deterministic,
network-free core — reading a provider's JSON shape into normalized messages, matching, extracting,
and the after-start selection — so it is fully gate-tested; the HTTP fetch (the only external
dependency) is injected at the handler layer (`orchestrator/types.py`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from bajutsu.scenario import EmailExtract, EmailMatch


@dataclass(frozen=True)
class MailboxMessage:
    """One inbox message, normalized from a provider's JSON via the configured field mapping.

    `id` is the message's stable identity (the provider's id, or a content hash when it exposes
    none) — the after-start baseline keys on it. `received_at` is the provider's timestamp string,
    used only to order newest-first; it is never compared to the local run clock (which would be
    skew-prone).
    """

    id: str
    to: str
    subject: str
    body: str
    received_at: str


def _field(raw: dict[str, Any], fields: dict[str, str], key: str, default_key: str) -> str:
    """Read one normalized field, following the configured path (or the default key) — "" if absent."""
    value = raw.get(fields.get(key, default_key))
    return value if isinstance(value, str) else ""


def read_messages(payload: Any, messages_path: str, fields: dict[str, str]) -> list[MailboxMessage]:
    """Normalize a provider's JSON response into messages. Pure; an unexpected shape yields none.

    `messages_path` is a dotted path to the message array (empty = the response is the array
    itself); `fields` maps each normalized field (`to` / `subject` / `body` / `receivedAt` / `id`)
    to the provider's key, defaulting to the field's own name. A message with no usable `id` gets a
    content-hash identity, so the after-start baseline still distinguishes it.
    """
    node: Any = payload
    for part in (p for p in messages_path.split(".") if p):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, list):
        return []
    out: list[MailboxMessage] = []
    for raw in node:
        if not isinstance(raw, dict):
            continue
        to = _field(raw, fields, "to", "to")
        subject = _field(raw, fields, "subject", "subject")
        body = _field(raw, fields, "body", "body")
        received_at = _field(raw, fields, "receivedAt", "receivedAt")
        mid = _field(raw, fields, "id", "id") or _synthetic_id(to, subject, body, received_at)
        out.append(
            MailboxMessage(id=mid, to=to, subject=subject, body=body, received_at=received_at)
        )
    return out


def _synthetic_id(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()


def match_message(msg: MailboxMessage, match: EmailMatch) -> bool:
    """Whether a message satisfies the `match` criteria (to / subject / subjectMatches), AND-ed."""
    if match.to is not None and msg.to != match.to:
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


def select(
    messages: list[MailboxMessage], match: EmailMatch, baseline_ids: frozenset[str]
) -> MailboxMessage | None:
    """The awaited message: matching, arrived after the step started, newest first. Pure.

    `baseline_ids` are the ids present when the step began; only ids absent from it are eligible, so
    a stale message left by an earlier run is never matched (skew-free — no clock comparison). Among
    eligible matches the newest `received_at` wins, with a stable tie-break on `id`.
    """
    eligible = [m for m in messages if m.id not in baseline_ids and match_message(m, match)]
    if not eligible:
        return None
    return max(eligible, key=lambda m: (m.received_at, m.id))
