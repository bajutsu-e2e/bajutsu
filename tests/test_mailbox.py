"""Tests for the pure mailbox logic behind the `email` step (bajutsu/mailbox.py, BE-0046).

`email` polls a generic HTTP mailbox, waits for a matching message that arrived *after* the step
started, and extracts a value by regex into `vars.*`. The matching, extraction, response-shape
reading, and after-start selection are pure deterministic functions tested here; the HTTP fetch
(the only external dependency) is injected at the handler layer.
"""

from __future__ import annotations

from bajutsu.mailbox import MailboxMessage, extract_value, match_message, read_messages, select
from bajutsu.scenario import EmailExtract, EmailMatch


def _msg(
    *, id: str = "1", to: str = "u@x.io", subject: str = "", body: str = "", at: str = ""
) -> MailboxMessage:
    return MailboxMessage(id=id, to=to, subject=subject, body=body, received_at=at)


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


def test_extract_returns_none_when_regex_misses() -> None:
    assert extract_value("no digits here", EmailExtract(var="c", bodyMatches=r"\d{6}")) is None


# --- response-shape reading (field mapping) ---


def test_read_messages_default_shape() -> None:
    payload = [
        {
            "to": "u@x.io",
            "subject": "Code",
            "body": "123456",
            "receivedAt": "2026-01-01T00:00:00Z",
            "id": "m1",
        }
    ]
    [m] = read_messages(payload, messages_path="", fields={})
    assert m.to == "u@x.io" and m.subject == "Code" and m.body == "123456"
    assert m.received_at == "2026-01-01T00:00:00Z" and m.id == "m1"


def test_read_messages_with_path_and_field_mapping() -> None:
    payload = {
        "items": [{"recipient": "u@x.io", "subj": "Code", "text": "999111", "ts": "t1", "uid": "x"}]
    }
    [m] = read_messages(
        payload,
        messages_path="items",
        fields={
            "to": "recipient",
            "subject": "subj",
            "body": "text",
            "receivedAt": "ts",
            "id": "uid",
        },
    )
    assert m.to == "u@x.io" and m.body == "999111" and m.received_at == "t1" and m.id == "x"


def test_read_messages_unexpected_shape_is_empty() -> None:
    assert read_messages({"items": "nope"}, messages_path="items", fields={}) == []
    assert read_messages(42, messages_path="", fields={}) == []  # type: ignore[arg-type]


def test_read_messages_synthesizes_id_when_absent() -> None:
    # no id field/value: identity is synthesized from content so the after-start baseline still works
    payload = [{"to": "u@x.io", "subject": "Code", "body": "111"}]
    [m] = read_messages(payload, messages_path="", fields={})
    assert m.id  # non-empty, stable identity


# --- after-start selection (id-set baseline, skew-free; newest by receivedAt) ---


def test_select_ignores_messages_present_at_start() -> None:
    old = _msg(id="old", subject="Code", body="000000", at="2026-01-01T00:00:00Z")
    new = _msg(id="new", subject="Code", body="123456", at="2026-01-01T00:01:00Z")
    m = EmailMatch(subjectMatches="Code")
    # baseline captured "old" at step start, so only "new" is eligible
    assert select([old, new], m, baseline_ids=frozenset({"old"})) == new
    # before the new one arrives, nothing eligible
    assert select([old], m, baseline_ids=frozenset({"old"})) is None


def test_select_picks_newest_among_eligible_matches() -> None:
    a = _msg(id="a", subject="Code", at="2026-01-01T00:01:00Z")
    b = _msg(id="b", subject="Code", at="2026-01-01T00:03:00Z")  # newer
    m = EmailMatch(subjectMatches="Code")
    assert select([a, b], m, baseline_ids=frozenset()) == b


def test_select_returns_none_when_no_eligible_match() -> None:
    a = _msg(id="a", subject="welcome", at="t")
    assert select([a], EmailMatch(subjectMatches="Code"), baseline_ids=frozenset()) is None


# --- handler + end-to-end (injected reader + fake clock; no network) ---

from conftest import el  # noqa: E402

from bajutsu.drivers import base  # noqa: E402
from bajutsu.drivers.fake import FakeDriver  # noqa: E402
from bajutsu.orchestrator import AlertGuardConfig, run_scenario  # noqa: E402
from bajutsu.orchestrator.loop import _do_email  # noqa: E402
from bajutsu.scenario import Scenario  # noqa: E402


class _FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


class _FakeMailbox:
    """Scripts a sequence of fetch() results; an Exception entry is raised when reached."""

    def __init__(self, *responses: object) -> None:
        self._responses = list(responses)
        self.calls = 0

    def fetch(self, timeout: float = 0) -> list[MailboxMessage]:
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[return-value]


def _email_step(**kw: object) -> Email:  # type: ignore[name-defined]
    from bajutsu.scenario import Email

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
        mailbox=_FakeMailbox(base.SelectorError("email: mailbox returned status 500")),
    )
    assert not result.ok
    assert "500" in (result.failure or "")


# --- model validation (load-time, BE-0046 review hardening) ---

import pytest  # noqa: E402

from bajutsu.scenario import Email  # noqa: E402


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


def test_on_blocked_retry_preserves_the_mailbox() -> None:
    # Regression: the retry-after-on_blocked path must still pass the mailbox, or a transient first
    # failure would be masked by a spurious "no mailbox configured" on the retry.
    from bajutsu.orchestrator import AlertEvent

    msg = _msg(id="new", subject="Verify", body="PIN 135790", at="2026-01-01T00:01:00Z")
    # First attempt (timeout 1s, ~1s poll) baselines + polls empty -> times out -> on_blocked fires
    # -> retry baselines empty then sees the message -> succeeds via the same mailbox. Were the retry
    # to drop the mailbox, it would report "no mailbox configured" instead.
    mailbox = _FakeMailbox([], [], [], [msg])
    scenario = Scenario.model_validate(
        {
            "name": "x",
            "steps": [
                {
                    "email": {
                        "match": {"subjectMatches": "Verify"},
                        "extract": {"var": "pin", "bodyMatches": r"PIN (\d+)"},
                        "timeout": 1,
                    }
                }
            ],
        }
    )

    def on_blocked(_d: base.Driver) -> AlertEvent | None:
        return AlertEvent(label="Allow")

    result = run_scenario(
        FakeDriver([el("home.title", "Home")]),
        scenario,
        clock=_FakeClock(),
        bindings={},
        mailbox=mailbox,
        alert_guard=AlertGuardConfig(vision=on_blocked),
    )
    assert result.ok, result.failure
    assert "no mailbox configured" not in (result.steps[0].reason or "")
