"""Read the app's reported exchanges and transitions into the evidence model."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .screen_transition import ScreenTransition

if TYPE_CHECKING:
    from .network_collector import NetworkCollector


# The receiver's known endpoints. Everything else POSTed is stored as a network exchange, which is
# why each of these has to be matched *before* that catch-all (BE-0365).
_TRANSITIONS_PATH = "/transitions"
_COMMANDS_PATH = "/commands"
_ACKNOWLEDGE_PATH = "/commands/ack"


def _no_transitions() -> list[tuple[ScreenTransition, float]]:
    return []


# C901 folds every method of the nested `Handler` class into this factory, so the score measures
# those request handlers, not branching here. Ruff bounds each method on its own, so the exemption
# loses no signal (BE-0386).
def _make_handler(collector: NetworkCollector) -> type[BaseHTTPRequestHandler]:  # noqa: C901
    class Handler(BaseHTTPRequestHandler):
        def _authenticated(self) -> bool:
            """True when the request bears this run's token; answers 401 itself when it does not.

            Rejecting loudly rather than dropping silently keeps a misconfigured client visible, and
            stops another local process from injecting fabricated exchanges (BE-0115) or reading the
            pending commands (BE-0365).
            """
            auth = self.headers.get("Authorization", "")
            presented = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
            if collector.check_token(presented):
                return True
            # Close rather than drain the unread body (mirrors serve's reject path). This
            # server is HTTP/1.0, so connections already close per request; the explicit flag
            # guards the reject path should the protocol ever be bumped to keep-alive.
            self.close_connection = True
            self.send_response(401)
            self.end_headers()
            return False

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
            # Authenticate before reading the body.
            if not self._authenticated():
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            route = self._route()
            # Ahead of the two report paths on purpose: the catch-all below stores any other path as
            # a network exchange, so falling through would put a control-channel acknowledgement
            # into the exchanges a `request` assertion reads (BE-0365).
            if route == _ACKNOWLEDGE_PATH:
                self._acknowledge(data)
                return
            if route == _COMMANDS_PATH or route.startswith(f"{_COMMANDS_PATH}/"):
                # The drain is a GET, so a POST anywhere in the channel's namespace is a mistake —
                # most plausibly an acknowledgement sent one path segment short. Answering it here
                # is what keeps it out of the catch-all: `NetworkExchange` defaults every field, so
                # any JSON object validates and would be stored as an all-empty exchange that a
                # `request` count assertion then sees.
                self.send_response(405 if route == _COMMANDS_PATH else 404)
                self.end_headers()
                return
            # /transitions (BE-0310) carries screen-transition events; every other path keeps the
            # original network-exchange behavior, so an app not yet linking the transition observer
            # is unaffected.
            add = collector.add_transition if route == _TRANSITIONS_PATH else collector.add
            # Accept a single record or a batch (list).
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    add(item)
            self.send_response(204)
            self.end_headers()

        def _acknowledge(self, data: Any) -> None:
            """Store the app's report on one command, answering 400 when bajutsu cannot read it."""
            if not isinstance(data, dict) or not collector.record_report(data):
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(204)
            self.end_headers()

        def do_GET(self) -> None:
            # Authenticated exactly as do_POST is: the pending commands are as much this run's
            # state as its exchanges, and no other local process may read or drain them (BE-0365).
            if not self._authenticated():
                return
            if self._route() == _COMMANDS_PATH:
                self._send_pending_commands()
                return
            # Nothing else is served over GET. Answering 404 rather than the bare 200 this handler
            # used to give every path is what stops an app polling a mistyped or version-skewed
            # path from reading an empty 200 as "no commands pending" — a hang with no evidence on
            # either side.
            self.send_response(404)
            self.end_headers()

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
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler
