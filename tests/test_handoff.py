"""Tests for the human-in-the-loop handoff contract and its CLI responders (BE-0179)."""

from __future__ import annotations

import io
import os
import sys
import types

import pytest

from bajutsu.cli.handoff import PromptHandoff, StreamHandoff, make_handoff
from bajutsu.handoff import (
    REQUEST_LINE_PREFIX,
    HandoffRequest,
    HandoffResponse,
    request_from_json,
    request_to_json,
    response_from_json,
    response_to_json,
)


def test_request_round_trips_including_screenshot() -> None:
    req = HandoffRequest(
        reason="enter OTP", screen="2 elements", target="#code", screenshot=b"\x89PNG\x00"
    )
    back = request_from_json(request_to_json(req))
    assert back == req


def test_request_round_trips_without_a_screenshot() -> None:
    back = request_from_json(request_to_json(HandoffRequest(reason="help")))
    assert back.reason == "help" and back.screenshot is None


@pytest.mark.parametrize(
    "response",
    [
        HandoffResponse(values=["999111"]),
        HandoffResponse(acted=True),
        HandoffResponse(cancelled=True),
    ],
)
def test_response_round_trips(response: HandoffResponse) -> None:
    assert response_from_json(response_to_json(response)) == response


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (HandoffResponse(cancelled=True), "cancel"),
        (HandoffResponse(cancelled=True, values=["x"]), "cancel"),  # cancel wins over a stray value
        (HandoffResponse(values=["x"]), "value"),
        (HandoffResponse(acted=True), "acted"),
        (HandoffResponse(), "acted"),
    ],
)
def test_response_kind_resolves_precedence(response: HandoffResponse, kind: str) -> None:
    assert response.kind == kind


def test_response_from_dict_coerces_untrusted_fields() -> None:
    response = HandoffResponse.from_dict({"values": [123], "acted": 1, "cancelled": 0})
    assert response.values == ["123"] and response.acted is True and response.cancelled is False


def test_response_from_dict_wraps_a_bare_string_value() -> None:
    # A bare string must be one value, never iterated char by char (an OTP is "123456", not 6 chars).
    assert HandoffResponse.from_dict({"values": "123456"}).values == ["123456"]
    assert HandoffResponse.from_dict({}).values == []


def test_make_handoff_routes_by_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    say = lambda _msg: None  # noqa: E731
    assert make_handoff("off", say=say) is None
    assert isinstance(make_handoff("prompt", say=say), PromptHandoff)
    assert isinstance(make_handoff("stream", say=say), StreamHandoff)
    # auto is interactive only at a TTY — else no responder (the clean-failure path for CI).
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    assert isinstance(make_handoff("auto", say=say), PromptHandoff)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    assert make_handoff("auto", say=say) is None


def _pipe_stdin(monkeypatch: pytest.MonkeyPatch, feed: bytes) -> None:
    """Wire sys.stdin to a real pipe pre-fed with *feed* (a real fd, so `select` can wait on it)."""
    read_fd, write_fd = os.pipe()
    if feed:
        os.write(write_fd, feed)
    os.close(write_fd)  # EOF after the fed bytes, so a bounded read never blocks past them
    monkeypatch.setattr(sys, "stdin", os.fdopen(read_fd, "r"))


def test_stream_handoff_emits_the_request_and_reads_the_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pipe_stdin(monkeypatch, (response_to_json(HandoffResponse(values=["999111"])) + "\n").encode())
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    response = StreamHandoff(timeout=2.0).request(HandoffRequest(reason="enter OTP", screen="1 el"))

    assert response.values == ["999111"]
    line = out.getvalue()
    assert line.startswith(REQUEST_LINE_PREFIX)
    assert request_from_json(line[len(REQUEST_LINE_PREFIX) :].strip()).reason == "enter OTP"


def test_stream_handoff_times_out_to_a_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _pipe_stdin(monkeypatch, b"")  # nothing to read
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert StreamHandoff(timeout=0.1).request(HandoffRequest(reason="x")).cancelled


def test_response_from_json_rejects_non_object_json() -> None:
    # Valid JSON that isn't an object must be a ValueError (not an AttributeError from `.get`), so
    # the StreamHandoff responder can map it to a cancel rather than crashing `record`.
    for payload in ("[]", "null", '"x"', "42"):
        with pytest.raises(ValueError, match="JSON object"):
            response_from_json(payload)


@pytest.mark.parametrize("payload", [b"not json at all\n", b"[]\n", b"null\n"])
def test_stream_handoff_treats_a_malformed_response_as_cancel(
    monkeypatch: pytest.MonkeyPatch, payload: bytes
) -> None:
    _pipe_stdin(monkeypatch, payload)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    assert StreamHandoff(timeout=2.0).request(HandoffRequest(reason="x")).cancelled


@pytest.mark.parametrize(
    ("typed", "check"),
    [
        (b"999111\n", lambda r: r.values == ["999111"]),
        (b"done\n", lambda r: r.acted),
        (b"\n", lambda r: r.acted),
        (b"cancel\n", lambda r: r.cancelled),
    ],
)
def test_prompt_handoff_interprets_the_typed_line(
    monkeypatch: pytest.MonkeyPatch, typed: bytes, check
) -> None:  # type: ignore[no-untyped-def]
    _pipe_stdin(monkeypatch, typed)
    assert check(PromptHandoff(lambda _m: None, timeout=2.0).request(HandoffRequest(reason="x")))


def test_prompt_handoff_times_out_to_a_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _pipe_stdin(monkeypatch, b"")
    assert PromptHandoff(lambda _m: None, timeout=0.1).request(HandoffRequest(reason="x")).cancelled
