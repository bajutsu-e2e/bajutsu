"""Tests for the pure mailbox logic behind the `email` step (bajutsu/common/mailbox.py).

Raw-message parsing, matching, extraction, and newest-first selection are pure deterministic
functions tested here, as is the step's poll loop over a fake reader; the IMAP / POP3 transport is
covered in `test_mailbox_registry.py`.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime, timedelta, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any

import pytest

from bajutsu.common import mailbox as mailbox_module
from bajutsu.common.mailbox import (
    MailboxMessage,
    UnknownNewestError,
    date_header,
    extract_value,
    match_message,
    parse_message,
    select,
)
from bajutsu.common.scenario import EmailExtract, EmailMatch


def _msg(
    *, id: str = "1", to: str = "u@x.io", subject: str = "", body: str = "", at: str = ""
) -> MailboxMessage:
    return MailboxMessage(id=id, to=(to,), subject=subject, body=body, received_at=at)


# --- matching ---


def test_match_to_and_subject_regex_are_anded() -> None:
    m = EmailMatch(to="u@x.io", subjectMatches="verif")
    assert match_message(_msg(to="u@x.io", subject="your verification code"), m)
    assert not match_message(_msg(to="other@x.io", subject="your verification code"), m)
    assert not match_message(_msg(to="u@x.io", subject="welcome"), m)


def test_match_subject_is_exact_while_subject_matches_is_regex() -> None:
    assert match_message(_msg(subject="Code"), EmailMatch(subject="Code"))
    assert not match_message(_msg(subject="Your Code"), EmailMatch(subject="Code"))  # exact
    assert match_message(_msg(subject="Your Code"), EmailMatch(subjectMatches="Code"))  # regex


def test_match_to_only() -> None:
    assert match_message(_msg(to="a@x.io"), EmailMatch(to="a@x.io"))
    assert not match_message(_msg(to="b@x.io"), EmailMatch(to="a@x.io"))


# --- extraction ---


def test_extract_first_capturing_group() -> None:
    val = extract_value(
        "Your code is 123456 now", EmailExtract(var="c", bodyMatches=r"code is (\d{6})")
    )
    assert val == "123456"


def test_extract_whole_match_when_no_group() -> None:
    assert extract_value("x 4815 y", EmailExtract(var="c", bodyMatches=r"\d{4}")) == "4815"


def test_extract_returns_group_one_with_multiple_capturing_groups() -> None:
    # With several groups the first (group 1) is the value, deterministically — the extra groups only
    # help the pattern match, they never change which one is bound. A trailing group must not win.
    val = extract_value("code 123-456 sent", EmailExtract(var="c", bodyMatches=r"(\d{3})-(\d{3})"))
    assert val == "123"


def test_extract_group_one_even_when_a_later_group_is_the_only_content() -> None:
    # group(1) is chosen by position, not by which group happened to capture the digits: an empty but
    # present first group returns "" (a clean miss for that step), never silently falling to group 2.
    val = extract_value("PIN: 999", EmailExtract(var="c", bodyMatches=r"(X*)(\d{3})"))
    assert val == ""


def test_extract_returns_none_when_regex_misses() -> None:
    assert extract_value("no digits here", EmailExtract(var="c", bodyMatches=r"\d{6}")) is None


def test_match_to_any_recipient_ignoring_case() -> None:
    m = MailboxMessage(id="1", to=("a@x.io", "U@X.io"), subject="", body="", received_at="")
    assert match_message(m, EmailMatch(to="u@x.io"))
    assert not match_message(m, EmailMatch(to="x.io"))  # a whole address, not a substring


# --- raw message parsing ---


def _parse(raw: bytes, received: datetime | None = None) -> MailboxMessage:
    return parse_message(raw, message_id="m1", received=received)


def test_parse_headers_decodes_subject_and_lists_every_recipient() -> None:
    raw = (
        b"To: Ann <a@x.io>, =?utf-8?q?B=2C_Smith?= <b@x.io>\r\n"
        b"Subject: =?utf-8?b?56K66KqN44Kz44O844OJ?=\r\n\r\ncode 123456\r\n"
    )
    m = _parse(raw)
    assert m.id == "m1"
    assert m.to == ("a@x.io", "b@x.io")
    assert m.subject == "確認コード"
    assert m.body == "code 123456\n"


def test_parse_joins_plain_parts_and_skips_attachments() -> None:
    msg = EmailMessage()
    msg.set_content("first 111")
    msg.add_alternative("<p>html 222</p>", subtype="html")
    msg.add_attachment("attached 333", filename="note.txt")
    forwarded = EmailMessage()
    forwarded.set_content("forwarded 444")
    msg.add_attachment(forwarded)
    body = _parse(msg.as_bytes()).body
    assert "111" in body
    assert not {"222", "333", "444"} & set(body.split())


def test_parse_falls_back_to_html_source() -> None:
    msg = EmailMessage()
    msg.set_content("<p>code <b>555</b></p>", subtype="html")
    assert "<b>555</b>" in _parse(msg.as_bytes()).body


def test_parse_decodes_the_declared_charset_and_survives_an_unknown_one() -> None:
    head = b"Content-Type: text/plain; charset=%s\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
    assert _parse(head % b"shift_jis" + "番号".encode("shift_jis")).body == "番号"
    assert _parse(head % b"x-nope" + b"123").body == "123"


def test_parse_tolerates_missing_and_malformed_headers() -> None:
    m = _parse(b"To: <<<\r\nSubject:\r\n\r\nbody")
    assert m.subject == "" and m.body == "body"
    assert all("@" not in a for a in m.to)


def _raise(*_args: object, **_kwargs: object) -> Any:
    raise IndexError("parser bug")


@pytest.mark.parametrize(
    ("broken", "lost"),
    [("get", {"subject", "body"}), ("get_all", {"to"}), ("iter_parts", {"body"})],
)
def test_what_the_stdlib_cannot_parse_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, broken: str, lost: set[str]
) -> None:
    msg = EmailMessage()
    msg["To"] = "u@x.io"
    msg["Subject"] = "Code"
    msg.set_content("111")
    msg.add_alternative("<p>222</p>", subtype="html")
    parsed_message = message_from_bytes(msg.as_bytes(), policy=policy.default)
    # Broken on the parsed object alone, so parsing the source itself still works.
    monkeypatch.setattr(parsed_message, broken, _raise)
    monkeypatch.setattr(mailbox_module, "message_from_bytes", lambda *a, **k: parsed_message)
    parsed = _parse(b"")
    values = {"subject": parsed.subject, "to": parsed.to, "body": parsed.body}
    assert {name for name, value in values.items() if not value} == lost


def test_parse_does_not_read_an_inline_embedded_message_as_this_ones_text() -> None:
    raw = (
        b"Content-Type: multipart/mixed; boundary=x\r\n\r\n"
        b"--x\r\nContent-Type: text/plain\r\n\r\nmine 111\r\n"
        b"--x\r\nContent-Type: message/rfc822\r\n\r\nSubject: old\r\n\r\ntheirs 222\r\n"
        b"--x--\r\n"
    )
    assert _parse(raw).body == "mine 111"


def test_parse_reads_a_multipart_with_no_parts_as_an_empty_body() -> None:
    assert _parse(b"Content-Type: multipart/mixed; boundary=x\r\n\r\n").body == ""


def test_a_date_that_leaves_the_calendar_in_utc_is_unusable() -> None:
    behind_utc = timezone(timedelta(hours=-1))
    assert _parse(b"\r\n", datetime(9999, 12, 31, 23, 59, tzinfo=behind_utc)).received_at == ""


def test_received_at_is_utc_iso8601_or_empty() -> None:
    jst = timezone(timedelta(hours=9))
    assert _parse(b"\r\n", datetime(2026, 1, 1, 9, 30, tzinfo=jst)).received_at == (
        "2026-01-01T00:30:00+00:00"
    )
    assert _parse(b"\r\n").received_at == ""


def test_date_header_reads_rfc5322_dates() -> None:
    assert date_header(b"Date: Thu, 01 Jan 2026 09:00:00 +0900\r\n\r\n") == datetime(
        2026, 1, 1, tzinfo=UTC
    )
    unzoned = date_header(b"Date: Thu, 01 Jan 2026 00:00:00 -0000\r\n\r\n")
    assert _parse(b"\r\n", unzoned).received_at == "2026-01-01T00:00:00+00:00"
    assert date_header(b"Date: yesterday\r\n\r\n") is None
    assert date_header(b"Subject: x\r\n\r\n") is None


# --- newest-match selection ---


def test_select_picks_newest_among_matches() -> None:
    a = _msg(id="a", subject="Code", at="2026-01-01T00:01:00+00:00")
    b = _msg(id="b", subject="Code", at="2026-01-01T00:03:00+00:00")  # newer
    assert select([b, a], EmailMatch(subjectMatches="Code")) == b


def test_select_returns_none_when_nothing_matches() -> None:
    a = _msg(id="a", subject="welcome")
    assert select([a], EmailMatch(subjectMatches="Code")) is None


def test_select_takes_a_lone_undated_match_but_refuses_to_rank_one() -> None:
    undated = _msg(id="z", subject="Code")
    dated = _msg(id="a", subject="Code", at="2026-01-01T00:00:00+00:00")
    unrelated = _msg(id="u", subject="welcome")
    m = EmailMatch(subjectMatches="Code")
    assert select([undated, unrelated], m) == undated
    with pytest.raises(UnknownNewestError, match="no date"):
        select([undated, dated], m)


def test_select_refuses_to_break_a_same_second_tie_by_id() -> None:
    # "7:9" sorts after "7:10", so an id tie-break would hand back the superseded code.
    at = "2026-01-01T00:00:00+00:00"
    pair = [_msg(id="7:9", subject="Code", at=at), _msg(id="7:10", subject="Code", at=at)]
    with pytest.raises(UnknownNewestError, match="share a receive time"):
        select(pair, EmailMatch(subject="Code"))
    newer = _msg(id="7:1", subject="Code", at="2026-01-01T00:00:01+00:00")
    assert select([*pair, newer], EmailMatch(subject="Code")) == newer


# --- handler + end-to-end (injected reader + fake clock; no network) ---

from conftest import el  # noqa: E402

from bajutsu.common.drivers import base  # noqa: E402
from bajutsu.common.drivers.fake import FakeDriver  # noqa: E402
from bajutsu.common.orchestrator import MailboxTimeout, run_scenario  # noqa: E402
from bajutsu.common.orchestrator.loop import _do_email  # noqa: E402
from bajutsu.common.scenario import Scenario  # noqa: E402


class _FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


class _FakeMailbox:
    """Scripts the inbox seen by each `list_ids()` call; an Exception entry is raised when reached."""

    def __init__(self, *inboxes: object) -> None:
        self._inboxes = list(inboxes)
        self._current: list[MailboxMessage] = []
        self.calls = 0
        self.fetched: list[set[str]] = []

    def list_ids(self, timeout: float) -> frozenset[str]:
        inbox = self._inboxes[min(self.calls, len(self._inboxes) - 1)]
        self.calls += 1
        if isinstance(inbox, Exception):
            raise inbox
        self._current = inbox  # type: ignore[assignment]
        return frozenset(m.id for m in self._current)

    def fetch(self, ids: Collection[str], timeout: float) -> list[MailboxMessage]:
        self.fetched.append(set(ids))
        return [m for m in self._current if m.id in ids]

    def check_same_inbox(self, baseline: frozenset[str], current: frozenset[str]) -> None:
        pass


def _email_step(**kw: Any) -> Email:
    from bajutsu.common.scenario import Email

    return Email.model_validate(kw)


def test_do_email_waits_for_a_new_matching_message_then_extracts() -> None:
    code = _msg(id="new", subject="Your code", body="code is 314159", at="2026-01-01T00:01:00Z")
    mailbox = _FakeMailbox([], [code])  # baseline empty, then the message arrives
    email = _email_step(
        match={"subjectMatches": "code"},
        extract={"var": "otp", "bodyMatches": r"code is (\d+)"},
        timeout=5,
    )
    bindings: dict[str, str] = {}
    ok, reason = _do_email(email, _FakeClock(), mailbox, bindings)
    assert ok and reason == ""
    assert bindings["vars.otp"] == "314159"


def test_do_email_ignores_mail_present_at_start_and_downloads_each_message_once() -> None:
    old = _msg(id="old", subject="Code", body="000000")
    noise = _msg(id="noise", subject="welcome")
    new = _msg(id="new", subject="Code", body="123456")
    mailbox = _FakeMailbox([old], [old, noise], [old, noise], [old, noise, new])
    email = _email_step(
        match={"subject": "Code"}, extract={"var": "c", "bodyMatches": r"\d+"}, timeout=10
    )
    bindings: dict[str, str] = {}
    assert _do_email(email, _FakeClock(), mailbox, bindings) == (True, "")
    assert bindings["vars.c"] == "123456"
    assert mailbox.fetched == [{"noise"}, {"new"}]


def test_do_email_fails_when_the_newest_match_is_unknown() -> None:
    a = _msg(id="a", subject="Code", body="1")
    b = _msg(id="b", subject="Code", body="2", at="2026-01-01T00:00:00+00:00")
    email = _email_step(
        match={"subject": "Code"}, extract={"var": "c", "bodyMatches": r"\d+"}, timeout=5
    )
    ok, reason = _do_email(email, _FakeClock(), _FakeMailbox([], [a, b]), {})
    assert not ok and "newest is unknown" in reason


def test_do_email_reports_a_last_poll_that_ran_out_of_time_as_a_timeout() -> None:
    email = _email_step(
        match={"to": "u@x.io"}, extract={"var": "c", "bodyMatches": r"\d+"}, timeout=5
    )
    mailbox = _FakeMailbox([], MailboxTimeout("email: IMAP did not answer"))
    ok, reason = _do_email(email, _FakeClock(), mailbox, {})
    assert not ok
    assert "no matching message within 5s (last poll: email: IMAP did not answer)" in reason


def test_do_email_times_out_cleanly() -> None:
    mailbox = _FakeMailbox([])  # nothing ever arrives
    email = _email_step(
        match={"to": "u@x.io"}, extract={"var": "c", "bodyMatches": r"\d+"}, timeout=2
    )
    ok, reason = _do_email(email, _FakeClock(), mailbox, {})
    assert not ok and "no matching message" in reason


def test_do_email_matched_but_extract_misses_fails() -> None:
    msg = _msg(id="n", subject="hi", body="no digits", at="t")
    email = _email_step(
        match={"subjectMatches": "hi"}, extract={"var": "c", "bodyMatches": r"\d{6}"}, timeout=5
    )
    ok, reason = _do_email(email, _FakeClock(), _FakeMailbox([], [msg]), {})
    assert not ok and "extract regex" in reason


def test_do_email_no_mailbox_configured_fails() -> None:
    email = _email_step(
        match={"to": "u@x.io"}, extract={"var": "c", "bodyMatches": r"\d+"}, timeout=5
    )
    ok, reason = _do_email(email, _FakeClock(), None, {})
    assert not ok and "no mailbox configured" in reason


def test_run_scenario_email_step_feeds_a_later_assert() -> None:
    # End-to-end: the extracted value lands in vars.* and a scenario-level expect consumes it.
    msg = _msg(id="new", subject="Verify", body="PIN 246810", at="2026-01-01T00:02:00Z")
    scenario = Scenario.model_validate(
        {
            "name": "otp login",
            "steps": [
                {
                    "email": {
                        "match": {"subjectMatches": "Verify"},
                        "extract": {"var": "pin", "bodyMatches": r"PIN (\d+)"},
                        "timeout": 10,
                    }
                }
            ],
        }
    )
    result = run_scenario(
        FakeDriver([el("home.title", "Home")]),
        scenario,
        clock=_FakeClock(),
        bindings={},
        mailbox=_FakeMailbox([], [msg]),
    )
    assert result.ok, result.failure
    assert result.steps[0].ok


def test_run_scenario_email_fetch_error_fails_the_step() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "x",
            "steps": [
                {
                    "email": {
                        "match": {"to": "u@x.io"},
                        "extract": {"var": "c", "bodyMatches": r"\d+"},
                        "timeout": 5,
                    }
                }
            ],
        }
    )
    result = run_scenario(
        FakeDriver([el("home.title", "Home")]),
        scenario,
        clock=_FakeClock(),
        bindings={},
        mailbox=_FakeMailbox(base.SelectorError("email: IMAP login rejected")),
    )
    assert not result.ok
    assert "login rejected" in (result.failure or "")


# --- model validation (load-time, BE-0046 review hardening) ---

from bajutsu.common.scenario import Email  # noqa: E402


def test_email_rejects_uncompilable_regex() -> None:
    with pytest.raises(ValueError, match="bodyMatches is not a valid regex"):
        Email.model_validate(
            {
                "match": {"to": "u@x.io"},
                "extract": {"var": "c", "bodyMatches": "([0-9]"},
                "timeout": 5,
            }
        )
    with pytest.raises(ValueError, match="subjectMatches is not a valid regex"):
        Email.model_validate(
            {
                "match": {"subjectMatches": "(?P<"},
                "extract": {"var": "c", "bodyMatches": r"\d+"},
                "timeout": 5,
            }
        )


def test_email_requires_positive_timeout() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        Email.model_validate(
            {
                "match": {"to": "u@x.io"},
                "extract": {"var": "c", "bodyMatches": r"\d+"},
                "timeout": 0,
            }
        )
