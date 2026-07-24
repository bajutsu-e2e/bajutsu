"""Network observation — the exchange model and the in-process collector.

How traffic is observed (DESIGN: network): a Simulator app runs as a host process
and shares the Mac's loopback, so the app POSTs each request/response it makes to a
small collector bajutsu runs on `127.0.0.1:<port>` (the port is injected into the app
via launch env, `BAJUTSU_COLLECTOR`, and a per-run shared token via
`BAJUTSU_COLLECTOR_TOKEN` — the collector accepts only POSTs bearing that token, so
another local process can't inject fabricated exchanges). The collector keeps the
exchanges in memory so a step's `request` assertion can be evaluated in real time, and
dumps them to `network.json` as scenario evidence.

The same receiver also accepts screen-transition reports on `/transitions`
(BE-0310): the opt-in `BajutsuScreen` observer in `BajutsuKit` (a
`UIViewController.viewDidAppear` hook) POSTs one record per completed appearance. They are
kept in an independent store from the network exchanges — the readiness gate and the
`settled` wait read only this one, never network-capture state, so the two stay independent
as documented.

The in-app side that captures and POSTs the exchanges is a separate Swift package
(`BajutsuKit`); this module is only the bajutsu-side receiver and data model.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class NetworkExchange(BaseModel):
    """One request/response the app reported.

    Extra keys from the SDK are ignored (forward-compatible); field names accept their JSON aliases.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    method: str = ""
    url: str = ""
    path: str = ""  # path only (no query), for matching
    status: int | None = None
    request_headers: dict[str, str] = Field(default_factory=dict, alias="requestHeaders")
    response_headers: dict[str, str] = Field(default_factory=dict, alias="responseHeaders")
    request_body: str | None = Field(default=None, alias="requestBody")
    response_body: str | None = Field(default=None, alias="responseBody")
    started_at: float | None = Field(default=None, alias="startedAt")
    duration_ms: float | None = Field(default=None, alias="durationMs")
    mocked: bool = False  # served by a bajutsu mock stub (not a real network call)


class ScreenTransition(BaseModel):
    """One screen-transition event the app's `BajutsuScreen` observer reported (BE-0310).

    Minimal by design: no screen content, only what a positive "the transition finished"
    signal needs. Extra keys are ignored and the app's own `timestamp` is informational only —
    the collector stamps its own receive time (`snapshot_timed`), the same monotonic clock
    domain the readiness gate and the `settled` wait already poll in, so nothing here depends on
    the app process's separate clock.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    kind: str = ""


# Returns the screen-transition events observed so far, each with the collector's receive time
# (its own monotonic clock, the same domain the readiness gate and the `settled` wait already poll
# in) — for `_await_ready` / `_wait_settled` (BE-0310) to consult as a read-only signal. Mirrors
# `Collector.snapshot_timed()`'s shape (below), not the untimed `orchestrator.types.NetworkSource`:
# both readiness and settled need the receive time itself (to bound "since this wait started" /
# compute the quiescence window), unlike a `request` assertion, which only needs exchange content.
# Kept here (not in `orchestrator`) since the readiness gate lives in `platform_lifecycle`, outside
# the orchestrator package.
TransitionSource = Callable[[], list[tuple[ScreenTransition, float]]]


def _no_transitions() -> list[tuple[ScreenTransition, float]]:
    return []


@runtime_checkable
class Collector(Protocol):
    """The exchange source the run loop and evidence writer drive.

    Independent of how it observed the traffic: the iOS `NetworkCollector` receives POSTs over HTTP;
    the web `WebNetworkCollector` hooks Playwright events — both satisfy this, so the pipeline stays
    backend-agnostic.
    """

    def snapshot(self) -> list[NetworkExchange]: ...  # observed exchanges, in arrival order
    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]: ...  # each + receive time
    def clear(self) -> None: ...  # drop observed exchanges (scoped per scenario by the run loop)
    def stop(self) -> None: ...  # release the observation resource (HTTP receiver / event hooks)
    # Screen-transition events (BE-0310), each with its receive time; independent of the exchanges
    # above. A collector with no such observer (web, fake) returns an empty list.
    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]: ...


class NetworkCollector:
    """Receives exchanges POSTed by the app and holds them for assertion + evidence.

    Thread-safe: the HTTP server runs on a background thread while the run loop reads
    `snapshot()` on the main thread. `clear()` between scenarios scopes the exchanges.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        # Each exchange with the monotonic time it was received (≈ completion), so the
        # report can place it on the scenario timeline.
        self._items: list[tuple[NetworkExchange, float]] = []
        # Screen-transition events (BE-0310), independent of the exchanges above — the readiness
        # gate and the `settled` wait read only this list, never `_items`.
        self._transitions: list[tuple[ScreenTransition, float]] = []
        self._now = now
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        # Per-run shared token, minted in start(); the app attaches it to every POST and the
        # handler rejects any request without it, so only the app this run launched can report.
        self.token = ""

    # --- data ---

    def add(self, data: dict[str, Any]) -> None:
        """Validate and store one reported exchange.

        A payload that fails validation is dropped rather than raised, so an SDK change can't break
        the run mid-flight (forward-compatible, matching `NetworkExchange`'s `extra="ignore"`).
        """
        try:
            ex = NetworkExchange.model_validate(data)
        except ValidationError:
            return
        with self._lock:
            self._items.append((ex, self._now()))

    def add_transition(self, data: dict[str, Any]) -> None:
        """Validate and store one reported screen-transition event (BE-0310).

        Same forward-compatible drop-on-failure behavior as `add`, and stored in its own list so
        the readiness/settled signal never depends on network-capture state.
        """
        try:
            transition = ScreenTransition.model_validate(data)
        except ValidationError:
            return
        with self._lock:
            self._transitions.append((transition, self._now()))

    def check_token(self, candidate: str) -> bool:
        """Constant-time compare of a presented token against this run's token.

        Mirrors `serve`'s own token check; false before `start()` mints a token.
        """
        return bool(self.token) and secrets.compare_digest(candidate, self.token)

    def snapshot(self) -> list[NetworkExchange]:
        """The exchanges received so far, in arrival order."""
        with self._lock:
            return [ex for ex, _ in self._items]

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        """Each exchange with its receive time (monotonic), in arrival order."""
        with self._lock:
            return list(self._items)

    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]:
        """Each observed screen-transition event with its receive time, in arrival order."""
        with self._lock:
            return list(self._transitions)

    def clear(self) -> None:
        """Drop all stored exchanges and transitions — called between scenarios to scope them to one run."""
        with self._lock:
            self._items.clear()
            self._transitions.clear()

    # --- lifecycle ---

    def start(self, port: int = 0) -> int:
        """Start the receiver on the loopback interface and begin accepting the app's POSTs.

        Args:
            port: TCP port to bind on `127.0.0.1`; `0` requests an ephemeral port.

        Returns:
            The actual bound port (resolved when `port` is `0`), to inject into the app via
            `BAJUTSU_COLLECTOR`.
        """
        self.token = secrets.token_urlsafe()
        server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(self))
        self.port = server.server_address[1]
        self._server = server
        # Poll often (vs the 0.5s default) so `stop()`'s shutdown() returns promptly — it blocks
        # until the loop's next poll tick. Speeds run teardown and the tests that start a collector.
        self._thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self.port

    def stop(self) -> None:
        """Stop the receiver and release its socket. Idempotent — a no-op if never started."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join()  # serve_forever has returned; join so no stale thread lingers
            self._thread = None
        self.port = 0


def _make_handler(collector: NetworkCollector) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            # Authenticate before reading the body: reject a missing/mismatched token loudly
            # (401) rather than dropping it silently, so a misconfigured client is visible and
            # another local process can't inject fabricated exchanges (BE-0115).
            auth = self.headers.get("Authorization", "")
            presented = auth[len("Bearer ") :] if auth.startswith("Bearer ") else ""
            if not collector.check_token(presented):
                # Close rather than drain the unread body (mirrors serve's reject path). This
                # server is HTTP/1.0, so connections already close per request; the explicit flag
                # guards the reject path should the protocol ever be bumped to keep-alive.
                self.close_connection = True
                self.send_response(401)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            # /transitions (BE-0310) carries screen-transition events; every other path keeps the
            # original network-exchange behavior, so an app not yet linking the transition observer
            # is unaffected. Compare on the path component alone (urlsplit drops a query string),
            # so an unexpected `?...` suffix still routes correctly instead of silently falling
            # through to `add` and being stored as a bogus network exchange.
            add = (
                collector.add_transition
                if urlsplit(self.path).path.rstrip("/") == "/transitions"
                else collector.add
            )
            # Accept a single record or a batch (list).
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    add(item)
            self.send_response(204)
            self.end_headers()

        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler
