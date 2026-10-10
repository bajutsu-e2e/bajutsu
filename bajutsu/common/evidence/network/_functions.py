"""Read the app's reported exchanges and transitions into the evidence model."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from typing import IO, TYPE_CHECKING, Any
from urllib.parse import urlsplit

from . import _hmac_auth
from .screen_transition import ScreenTransition

if TYPE_CHECKING:
    from .network_collector import NetworkCollector


# The receiver's known endpoints. Everything else POSTed is stored as a network exchange, which is
# why each of these has to be matched *before* that catch-all (BE-0365).
_TRANSITIONS_PATH = "/transitions"
_COMMANDS_PATH = "/commands"
_ACKNOWLEDGE_PATH = "/commands/ack"
# The app's reachability probe for a real device offered several host addresses: authenticated like
# every route, and stateless, unlike `/commands`, whose GET drains the queue.
_PING_PATH = "/ping"

# What a signed request's body may hold in memory before its signature is checked; past this the
# spool moves to a temporary file (BE-0459). The chunk is the read size that feeds both.
_SPOOL_MEMORY_BYTES = 1 << 20
_READ_CHUNK_BYTES = 64 << 10


def _no_transitions() -> list[tuple[ScreenTransition, float]]:
    return []


# C901 folds every method of the nested `Handler` class into this factory, so the score measures
# those request handlers, not branching here. Ruff bounds each method on its own, so the exemption
# loses no signal (BE-0386).
def _make_handler(collector: NetworkCollector) -> type[BaseHTTPRequestHandler]:  # noqa: C901
    class Handler(BaseHTTPRequestHandler):
        # The verified nonce of the request being answered, or None for a bearer request (BE-0459).
        _nonce: str | None = None

        def _authenticate(self) -> bytes | None:
            """The request's body once it proves this run's token; None after answering 400/401/409.

            Rejecting loudly rather than dropping silently keeps a misconfigured client visible, and
            stops another process from injecting fabricated exchanges (BE-0115) or reading the
            pending commands (BE-0365). A signed request (BE-0459) also sets `_nonce`, which every
            later answer to it is signed against.
            """
            self._nonce = None
            length = self._content_length()
            if length is None:
                self._refuse(400)
                return None
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                if collector.check_bearer(auth[len("Bearer ") :]):
                    return self.rfile.read(length) if length else b""
                self._refuse(401)
                return None
            parsed = _hmac_auth.parse_authorization(auth)
            if parsed is None:
                self._refuse(401)
                return None
            nonce, signature = parsed
            # A spent nonce is refused before the body is read; `claim_nonce` below re-checks it
            # atomically, so this early answer is only a shortcut, never the guarantee.
            if collector.nonce_spent(nonce):
                self._refuse(409)
                return None
            with self._spool_body(length) as (spool, digest):
                expected = _hmac_auth.sign_request(
                    collector.token,
                    self.command,
                    _hmac_auth.canonical_path(self.path),
                    nonce,
                    digest,
                )
                if not collector.token or not _hmac_auth.signatures_match(expected, signature):
                    self._refuse(401)
                    return None
                # Recorded only once the signature matched, so a forgery that reuses a nonce read
                # in transit cannot lock the genuine request out; of two concurrent copies, the one
                # that loses this race gets the 409.
                if not collector.claim_nonce(nonce):
                    self._refuse(409)
                    return None
                self._nonce = nonce
                spool.seek(0)
                return spool.read()

        def _content_length(self) -> int | None:
            """The declared body length, or None when it is not a non-negative integer.

            A malformed value would otherwise raise inside the handler and leave the client with no
            answer at all, and a negative one would read the socket to its end.
            """
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            return length if length >= 0 else None

        @contextmanager
        def _spool_body(self, length: int) -> Iterator[tuple[IO[bytes], str]]:
            """The body, hashed as it is read and spooled to disk past a fixed memory bound.

            The collector caps no body — a report cut mid-object would fail a `responseSchema`
            assertion on a valid payload — so a sender without the token may still send a large
            one. Spooling bounds what that costs in memory before the signature is checked; the
            spool is dropped on every exit, the mismatch included (BE-0459).
            """
            digest = hashlib.sha256()
            with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_BYTES) as spool:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, _READ_CHUNK_BYTES))
                    if not chunk:
                        break  # a short body fails the digest, so the signature check refuses it
                    digest.update(chunk)
                    spool.write(chunk)
                    remaining -= len(chunk)
                yield spool, digest.hexdigest()

        def _refuse(self, status: int) -> None:
            """Answer a refusal unsigned: none has a verified request to bind a signature to."""
            # Close rather than drain an unread body (mirrors serve's reject path). This server is
            # HTTP/1.0, so connections already close per request; the explicit flag guards the
            # reject path should the protocol ever be bumped to keep-alive.
            self.close_connection = True
            self.send_response(status)
            self.end_headers()

        def _answer(self, status: int, body: bytes = b"", content_type: str | None = None) -> None:
            """Send one answer, signed against the request's nonce when the request was signed."""
            self.send_response(status)
            if self._nonce is not None:
                self.send_header(
                    _hmac_auth.ANSWER_HEADER,
                    _hmac_auth.sign_answer(collector.token, self._nonce, status, body),
                )
            if content_type is not None:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _route(self) -> str:
            """The request's path alone, without a query string or a trailing slash.

            `urlsplit` drops the query, so an unexpected `?...` suffix still routes to its endpoint
            instead of falling through to the catch-all and being stored as a bogus exchange. It is
            safe to hand `urlsplit` a request-line path even though it reads a leading `//` as an
            authority: `BaseHTTPRequestHandler` has already collapsed one (CPython gh-87389), and
            `test_a_doubled_leading_slash_still_reaches_its_endpoint` is what fails if it stops.
            """
            return urlsplit(self.path).path.rstrip("/")

        def do_POST(self) -> None:
            # Authenticate before using the body.
            raw = self._authenticate()
            if raw is None:
                return
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._answer(400)
                return
            route = self._route()
            # Ahead of the two report paths on purpose: the catch-all below stores any other path as
            # a network exchange, so falling through would put a control-channel acknowledgement
            # into the exchanges a `request` assertion reads (BE-0365).
            if route == _ACKNOWLEDGE_PATH:
                self._acknowledge(data)
                return
            if route == _PING_PATH:
                # The probe is a GET; a POST here must not land in the catch-all as an exchange.
                self._answer(405)
                return
            if route == _COMMANDS_PATH or route.startswith(f"{_COMMANDS_PATH}/"):
                # The drain is a GET, so a POST anywhere in the channel's namespace is a mistake —
                # most plausibly an acknowledgement sent one path segment short. Answering it here
                # is what keeps it out of the catch-all: `NetworkExchange` defaults every field, so
                # any JSON object validates and would be stored as an all-empty exchange that a
                # `request` count assertion then sees.
                self._answer(405 if route == _COMMANDS_PATH else 404)
                return
            # /transitions (BE-0310) carries screen-transition events; every other path keeps the
            # original network-exchange behavior, so an app not yet linking the transition observer
            # is unaffected.
            add = collector.add_transition if route == _TRANSITIONS_PATH else collector.add
            # Accept a single record or a batch (list).
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    add(item)
            self._answer(204)

        def _acknowledge(self, data: Any) -> None:
            """Store the app's report on one command, answering 400 when bajutsu cannot read it."""
            if not isinstance(data, dict) or not collector.record_report(data):
                self._answer(400)
                return
            self._answer(204)

        def do_GET(self) -> None:
            # Authenticated exactly as do_POST is: the pending commands are as much this run's
            # state as its exchanges, and no other local process may read or drain them (BE-0365).
            if self._authenticate() is None:
                return
            if self._route() == _COMMANDS_PATH:
                self._send_pending_commands()
                return
            if self._route() == _PING_PATH:
                self._answer(204)
                return
            # Nothing else is served over GET. Answering 404 rather than the bare 200 this handler
            # used to give every path is what stops an app polling a mistyped or version-skewed
            # path from reading an empty 200 as "no commands pending" — a hang with no evidence on
            # either side.
            self._answer(404)

        def _send_pending_commands(self) -> None:
            """Hand the app every pending command, emptying the queue in the same step."""
            body = json.dumps(
                [
                    # Alias keys without unset fields: a stub table's mocks then match the
                    # `BAJUTSU_MOCKS` shape BajutsuKit already parses (`dump_mocks`).
                    command.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for command in collector.drain_commands()
                ]
            ).encode()
            self._answer(200, body, "application/json")

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler
