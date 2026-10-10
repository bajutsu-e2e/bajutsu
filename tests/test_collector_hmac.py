"""The collector's signed scheme (BE-0459): canonical forms, the handler, and the bearer policy."""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from bajutsu.common.evidence.network import (
    InAppCapability,
    NetworkCollector,
    _functions,
    _hmac_auth,
    network_collector,
)

_VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "be0459" / "collector_hmac_vectors.json").read_text()
)


# --- canonical forms (unit 1) ---


@pytest.mark.parametrize("vector", _VECTORS["requests"], ids=lambda v: f"{v['method']} {v['path']}")
def test_request_vectors_sign_to_their_pinned_signature(vector: dict[str, Any]) -> None:
    digest = hashlib.sha256(vector["body"].encode()).hexdigest()
    assert (
        _hmac_auth.sign_request(
            vector["token"], vector["method"], vector["path"], vector["nonce"], digest
        )
        == vector["signature"]
    )


@pytest.mark.parametrize("vector", _VECTORS["answers"], ids=lambda v: str(v["status"]))
def test_answer_vectors_sign_to_their_pinned_signature(vector: dict[str, Any]) -> None:
    assert (
        _hmac_auth.sign_answer(
            vector["token"], vector["nonce"], vector["status"], vector["body"].encode()
        )
        == vector["signature"]
    )


@pytest.mark.parametrize("case", _VECTORS["paths"], ids=lambda c: c["target"] or "<empty>")
def test_canonical_path_drops_the_query_and_defaults_to_root(case: dict[str, str]) -> None:
    assert _hmac_auth.canonical_path(case["target"]) == case["path"]


def test_the_report_vector_covers_the_collectors_bare_url() -> None:
    assert any(v["method"] == "POST" and v["path"] == "/" for v in _VECTORS["requests"])


def test_a_built_header_parses_back_to_its_nonce_and_signature() -> None:
    header = _hmac_auth.authorization("tok", "GET", "/ping", b"", "n0nce")
    parsed = _hmac_auth.parse_authorization(header)
    assert parsed is not None
    nonce, signature = parsed
    assert nonce == "n0nce"
    assert signature == _hmac_auth.sign_request(
        "tok", "GET", "/ping", "n0nce", hashlib.sha256(b"").hexdigest()
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Bearer tok",
        "Bajutsu-HMAC-SHA256",
        "Bajutsu-HMAC-SHA256 nonce=a",
        "Bajutsu-HMAC-SHA256 nonce=a, signature=",
        "Bajutsu-HMAC-SHA256 nonce=a, nonce=b, signature=c",
        "Bajutsu-HMAC-SHA256 nonce=a, signature=b, extra=c",
        "bajutsu-hmac-sha256 nonce=a, signature=b",
    ],
)
def test_a_malformed_header_parses_to_none(value: str) -> None:
    assert _hmac_auth.parse_authorization(value) is None


def test_nonces_are_fresh_and_sixteen_bytes() -> None:
    nonces = {_hmac_auth.new_nonce() for _ in range(64)}
    assert len(nonces) == 64
    assert all(len(n) == 22 and "=" not in n for n in nonces)  # 16 bytes, unpadded base64url


def test_signatures_match_is_false_for_non_ascii_rather_than_raising() -> None:
    assert not _hmac_auth.signatures_match("abc", "äbc")


# --- the handler (unit 2) ---


class _Answer:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body


# What `_send` reports when the collector closed the connection before the client finished writing:
# a refusal that leaves the body unread (401/409) closes rather than drains it, so a client still
# sending can see the reset before it reads the status.
_RESET = -1


def _send(
    port: int,
    method: str,
    target: str,
    body: bytes = b"",
    *,
    authorization: str | None = None,
) -> _Answer:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {"Content-Length": str(len(body))}
        if authorization is not None:
            headers["Authorization"] = authorization
        try:
            conn.request(method, target, body=body, headers=headers)
            resp = conn.getresponse()
            return _Answer(resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read())
        except (ConnectionResetError, BrokenPipeError):
            return _Answer(_RESET, {}, b"")
    finally:
        conn.close()


def _signed(
    port: int, token: str, method: str, target: str, body: bytes = b"", nonce: str | None = None
) -> tuple[_Answer, str]:
    nonce = nonce or _hmac_auth.new_nonce()
    header = _hmac_auth.authorization(token, method, target, body, nonce)
    return _send(port, method, target, body, authorization=header), nonce


def _answer_verifies(answer: _Answer, token: str, nonce: str) -> bool:
    presented = answer.headers.get(_hmac_auth.ANSWER_HEADER.lower())
    expected = _hmac_auth.sign_answer(token, nonce, answer.status, answer.body)
    return presented is not None and _hmac_auth.signatures_match(expected, presented)


_REPORT = json.dumps({"method": "GET", "url": "https://example.test/a", "status": 200}).encode()


@pytest.fixture
def collector() -> Iterator[NetworkCollector]:
    c = NetworkCollector()
    c.start()
    try:
        yield c
    finally:
        c.stop()


def test_a_matching_signature_is_accepted_and_its_answer_signed(
    collector: NetworkCollector,
) -> None:
    answer, nonce = _signed(collector.port, collector.token, "POST", "/", _REPORT)
    assert answer.status == 204
    assert _answer_verifies(answer, collector.token, nonce)
    assert [ex.url for ex in collector.snapshot()] == ["https://example.test/a"]


@pytest.mark.parametrize(
    ("signed_as", "sent_as"),
    [
        (("POST", "/", _REPORT), ("POST", "/", _REPORT.replace(b"200", b"500"))),
        (("GET", "/ping", b"{}"), ("POST", "/ping", b"{}")),
        (("POST", "/", _REPORT), ("POST", "/transitions", _REPORT)),
    ],
    ids=["body", "method", "path"],
)
def test_a_signature_that_does_not_cover_the_request_sent_is_refused(
    collector: NetworkCollector,
    signed_as: tuple[str, str, bytes],
    sent_as: tuple[str, str, bytes],
) -> None:
    header = _hmac_auth.authorization(collector.token, *signed_as, _hmac_auth.new_nonce())
    method, target, body = sent_as
    answer = _send(collector.port, method, target, body, authorization=header)
    # Each case sends a bodied request whose signature check reads the body first, so the 401 is
    # read rather than raced by a reset.
    assert answer.status == 401
    assert _hmac_auth.ANSWER_HEADER.lower() not in answer.headers
    assert collector.snapshot() == []
    assert collector.nonce_count == 0


@pytest.mark.parametrize("length", ["abc", "-5"])
def test_a_malformed_content_length_gets_400_rather_than_no_answer(
    collector: NetworkCollector, length: str
) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", collector.port, timeout=10)
    try:
        conn.putrequest("GET", "/ping")
        conn.putheader("Content-Length", length)
        conn.putheader("Authorization", f"Bearer {collector.token}")
        conn.endheaders()
        assert conn.getresponse().status == 400
    finally:
        conn.close()


def test_a_wrong_token_is_refused(collector: NetworkCollector) -> None:
    answer, _ = _signed(collector.port, "not-the-token", "POST", "/", _REPORT)
    assert answer.status == 401
    assert collector.snapshot() == []


@pytest.mark.parametrize(
    "authorization", [None, "", "Basic abc", "Bajutsu-HMAC-SHA256 nonce=a"], ids=repr
)
def test_a_missing_or_malformed_header_gets_401(
    collector: NetworkCollector, authorization: str | None
) -> None:
    # A bodiless GET: a refusal before the body is read can otherwise race a reset.
    answer = _send(collector.port, "GET", "/ping", authorization=authorization)
    assert answer.status == 401


def test_a_replayed_request_gets_409_unsigned(collector: NetworkCollector) -> None:
    # A bodiless GET, so the refusal is read rather than raced by a reset.
    first, nonce = _signed(collector.port, collector.token, "GET", "/ping")
    replay, _ = _signed(collector.port, collector.token, "GET", "/ping", nonce=nonce)
    assert first.status == 204
    assert replay.status == 409
    assert _hmac_auth.ANSWER_HEADER.lower() not in replay.headers


def test_a_replayed_report_adds_nothing(collector: NetworkCollector) -> None:
    first, nonce = _signed(collector.port, collector.token, "POST", "/", _REPORT)
    replay, _ = _signed(collector.port, collector.token, "POST", "/", _REPORT, nonce=nonce)
    assert first.status == 204
    assert replay.status in (409, _RESET)
    assert len(collector.snapshot()) == 1


def test_a_request_captured_in_one_scenario_stays_refused_in_the_next(
    collector: NetworkCollector,
) -> None:
    _, nonce = _signed(collector.port, collector.token, "POST", "/", _REPORT)
    collector.clear()
    replay, _ = _signed(collector.port, collector.token, "POST", "/", _REPORT, nonce=nonce)
    assert replay.status in (409, _RESET)
    assert collector.snapshot() == []


def test_a_forgery_reusing_a_nonce_does_not_lock_out_the_genuine_request(
    collector: NetworkCollector,
) -> None:
    nonce = _hmac_auth.new_nonce()
    forged = _send(
        collector.port,
        "POST",
        "/",
        _REPORT,
        authorization=f"{_hmac_auth.SCHEME} nonce={nonce}, signature=AAAA",
    )
    genuine, _ = _signed(collector.port, collector.token, "POST", "/", _REPORT, nonce=nonce)
    assert forged.status == 401
    assert genuine.status == 204


def test_of_concurrent_copies_of_one_request_exactly_one_is_accepted(
    collector: NetworkCollector,
) -> None:
    nonce = _hmac_auth.new_nonce()
    header = _hmac_auth.authorization(collector.token, "POST", "/", _REPORT, nonce)
    copies = 4  # within the server's listen backlog (5), so no copy is reset
    barrier = threading.Barrier(copies)
    statuses: list[int] = []

    def send_copy() -> None:
        barrier.wait()
        statuses.append(_send(collector.port, "POST", "/", _REPORT, authorization=header).status)

    threads = [threading.Thread(target=send_copy) for _ in range(copies)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses.count(204) == 1
    assert set(statuses) <= {204, 409, _RESET}
    assert len(collector.snapshot()) == 1


def test_claim_nonce_admits_exactly_one_of_many_concurrent_claims() -> None:
    c = NetworkCollector()
    claims = 32
    barrier = threading.Barrier(claims)
    won: list[bool] = []

    def claim() -> None:
        barrier.wait()
        won.append(c.claim_nonce("n"))

    threads = [threading.Thread(target=claim) for _ in range(claims)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert won.count(True) == 1


def test_an_unsigned_large_body_is_spooled_past_the_memory_bound_then_dropped(
    collector: NetworkCollector, monkeypatch: pytest.MonkeyPatch
) -> None:
    spools: list[Any] = []
    real = tempfile.SpooledTemporaryFile

    def recording(*args: Any, **kwargs: Any) -> Any:
        spool = real(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(_functions, "_SPOOL_MEMORY_BYTES", 4096)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", recording)
    body = b"x" * (256 << 10)
    header = _hmac_auth.authorization(collector.token, "POST", "/", b"other", "n")
    answer = _send(collector.port, "POST", "/", body, authorization=header)
    assert answer.status in (401, _RESET)
    (spool,) = spools
    assert spool._rolled  # moved to disk instead of growing in memory
    assert spool.closed
    assert collector.nonce_count == 0


def test_a_large_signed_body_is_recorded_whole(collector: NetworkCollector) -> None:
    payload = "y" * (3 << 20)  # past the default in-memory spool
    body = json.dumps({"method": "GET", "url": "https://example.test/big", "responseBody": payload})
    answer, _ = _signed(collector.port, collector.token, "POST", "/", body.encode())
    assert answer.status == 204
    (exchange,) = collector.snapshot()
    assert exchange.response_body == payload


@pytest.mark.parametrize(
    ("method", "target", "body", "status"),
    [
        ("GET", "/ping", b"", 204),
        ("GET", "/commands", b"", 200),
        ("GET", "/nowhere", b"", 404),
        ("POST", "/ping", b"{}", 405),
        ("POST", "/", b"not json", 400),
        ("POST", "/transitions", b'{"kind":"screenChanged","timestamp":1}', 204),
        ("POST", "/commands/ack", b"[]", 400),
    ],
)
def test_every_authenticated_answer_carries_a_signature_bound_to_its_nonce(
    collector: NetworkCollector, method: str, target: str, body: bytes, status: int
) -> None:
    collector.enqueue_command(InAppCapability.TOUCH_VISUALIZATION, enabled=True)
    answer, nonce = _signed(collector.port, collector.token, method, target, body)
    assert answer.status == status
    assert _answer_verifies(answer, collector.token, nonce)
    # Bound to this request: the same answer does not verify against any other nonce.
    assert not _answer_verifies(answer, collector.token, _hmac_auth.new_nonce())


def test_the_drained_commands_arrive_in_a_signed_answer(collector: NetworkCollector) -> None:
    command_id = collector.enqueue_command(InAppCapability.TOUCH_VISUALIZATION, enabled=True)
    answer, nonce = _signed(collector.port, collector.token, "GET", "/commands")
    assert _answer_verifies(answer, collector.token, nonce)
    assert [c["id"] for c in json.loads(answer.body)] == [command_id]


# --- the bearer policy (unit 3) ---


def test_the_loopback_binding_still_accepts_the_bearer_header(
    collector: NetworkCollector,
) -> None:
    answer = _send(collector.port, "POST", "/", _REPORT, authorization=f"Bearer {collector.token}")
    assert answer.status == 204
    assert _hmac_auth.ANSWER_HEADER.lower() not in answer.headers
    assert len(collector.snapshot()) == 1
    assert collector.refused_bearer_count == 0


def test_an_all_interface_binding_refuses_the_runs_bearer_counts_it_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c = NetworkCollector()
    port = c.start(host="::")
    try:
        with caplog.at_level(logging.INFO):
            answers = [
                _send(port, "GET", "/ping", authorization=f"Bearer {c.token}") for _ in range(2)
            ]
            signed, _ = _signed(port, c.token, "POST", "/", _REPORT)
        assert [a.status for a in answers] == [401, 401]
        assert signed.status == 204
        assert c.refused_bearer_count == 2
    finally:
        with caplog.at_level(logging.INFO):
            c.stop()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # one warning however many bearers were refused
    assert "predates signed requests" in warnings[0].getMessage()
    assert any(
        "accepted 1 signed requests and refused 2 bearer requests" in r.getMessage()
        for r in caplog.records
    )


def test_an_all_interface_binding_refuses_a_made_up_bearer_without_counting_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    c = NetworkCollector()
    port = c.start(host="::")
    try:
        answer = _send(port, "GET", "/ping", authorization="Bearer made-up")
        assert answer.status == 401
        assert c.refused_bearer_count == 0
    finally:
        with caplog.at_level(logging.INFO):
            c.stop()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("::", False),
        ("0.0.0.0", False),
        ("devbox.local", False),  # not an address: refuse the bearer rather than guess
    ],
)
def test_only_a_loopback_binding_accepts_the_bearer(host: str, loopback: bool) -> None:
    assert network_collector._is_loopback(host) is loopback
