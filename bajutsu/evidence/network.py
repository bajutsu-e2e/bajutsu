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

The same receiver also carries the in-app control channel (BE-0365): bajutsu queues a command
naming one piece of its own in-app instrumentation and the state that piece should take, the app
drains the queue over an authenticated `GET /commands`, and reports back on `/commands/ack` whether
it applied the command.
That direction is what lets a capability change *within* a scenario rather than only at launch, and
it needs no new server, port, or authentication scheme — the app opens no socket, and the per-run
token above guards the commands exactly as it guards the reports. The channel carries no judgement:
nothing on it may influence whether a step passes, and no assertion reads from it.

The in-app side that captures and POSTs the exchanges is a separate Swift package
(`BajutsuKit`); this module is only the bajutsu-side receiver and data model.
"""

from __future__ import annotations

import errno
import json
import secrets
import threading
import time
from collections.abc import Callable
from enum import StrEnum
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


class InAppCapability(StrEnum):
    """A piece of bajutsu's own in-app instrumentation the control channel may address (BE-0365).

    Closed on purpose, and that is the boundary rather than a comment about it: the channel controls
    what bajutsu put inside the app, never the application's own state. A command that seeded app
    data or drove navigation would move per-app knowledge into the tool (prime directive 3), so a
    new capability is argued for here instead of being named as a free string at a call site.
    """

    TOUCH_VISUALIZATION = "touch_visualization"  # the touch markers BE-0371 draws


class AppCommand(BaseModel):
    """One command bajutsu asks the running app to apply (BE-0365).

    bajutsu-side only — the collector serializes these out and never parses one back, so this model
    is strict and frozen rather than forward-compatible like the reports the app POSTs. It carries
    no judgement: nothing here may influence whether a step passes, and no assertion reads it.

    `enabled` is the whole state a capability takes today, because the instrumentation the channel
    reaches is a toggle. A capability whose state is not a toggle (a mid-scenario stub table,
    BE-0365 unit 4) arrives as a sibling model discriminated on `capability`, not as another
    optional field here: widening this one would make the invalid cross-product — a stub table with
    no table, a toggle carrying one — representable, and leave a validator to rule out what a union
    rules out structurally (the shape `config/effective.py` already argues for).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    capability: InAppCapability
    enabled: bool


class AppCommandReport(BaseModel):
    """The app's report on one command it drained (BE-0365).

    Inbound, so forward-compatible like the exchange and transition reports — but `applied` carries
    no default, because "applied it" and "drained it and could not apply it" must not reach the
    acknowledgement wait as the same message, and a default would quietly make one of them the
    other. An app whose capability was compiled out, or whose handler raised, says so here with its
    own `reason`, so the wait fails with the cause rather than timing out blind (BE-0365 unit 3).
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore", frozen=True)

    id: str
    applied: bool
    reason: str = ""


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
    # Also drops the transition events below, and on a collector carrying the in-app control
    # channel a command left undrained plus its report (BE-0365).
    def clear(self) -> None: ...  # drop what the run loop scopes to one scenario
    def stop(self) -> None: ...  # release the observation resource (HTTP receiver / event hooks)
    # Screen-transition events (BE-0310), each with its receive time; independent of the exchanges
    # above. A collector with no such observer (web, fake) returns an empty list.
    def transitions_snapshot_timed(self) -> list[tuple[ScreenTransition, float]]: ...


# The band `start_bridgeable` draws the collector's port from — below both the host's and the
# emulator's ephemeral ranges, and beside the resident UI Automator server's device port
# (`adb.RESIDENT_DEVICE_PORT`, 6790), which reserves a fixed port for the same reason. The span is
# the ceiling on collectors sharing one host: one per leased device, so it covers every parallel
# lane a run can hold and every other bajutsu process on the machine.
_BRIDGE_PORT_BASE = 6800
_BRIDGE_PORT_SPAN = 100


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
        # The control channel (BE-0365): commands waiting for the app to drain, every id issued,
        # and the subset the app has reported applying.
        self._commands: list[AppCommand] = []
        self._issued: set[str] = set()
        self._reports: dict[str, AppCommandReport] = {}
        # Monotonic for this collector's whole life and deliberately *not* reset by `clear()`: a
        # reused id would let a cleared scenario's late acknowledgement match a fresh command and
        # release its wait without the app having applied anything.
        self._issued_count = 0

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

    def enqueue_command(self, capability: InAppCapability, *, enabled: bool) -> str:
        """Queue one command for the app to drain, and return the id that identifies it.

        Args:
            capability: which piece of bajutsu's in-app instrumentation the command addresses.
            enabled: the state that capability should take.

        Returns:
            The command's id, to condition-wait on through `report_for` (BE-0365 unit 3).
        """
        with self._lock:
            self._issued_count += 1
            command_id = f"c{self._issued_count}"
            self._commands.append(AppCommand(id=command_id, capability=capability, enabled=enabled))
            self._issued.add(command_id)
            return command_id

    def drain_commands(self) -> list[AppCommand]:
        """Take every pending command, leaving the queue empty.

        Draining under the lock bounds delivery at *at most* once: two polls racing cannot both take
        the same command and have the app apply it twice. It buys nothing about the reply — the queue
        is emptied before the response is written, so a reply lost in flight (a killed app, a client
        timeout, a reset peer) is not redelivered. That loss surfaces as the acknowledgement wait's
        loud timeout (BE-0365 unit 3), never as a second application, so a caller must not read a
        successful drain as proof the app received anything.
        """
        with self._lock:
            drained = self._commands
            self._commands = []
            return drained

    def record_report(self, data: dict[str, Any]) -> bool:
        """Store the app's report on one command; false when it names no command this run issued.

        Refusing a payload rather than dropping it is the one place this collector departs from
        `add` / `add_transition`'s forward-compatible drop: a report is the only news the
        acknowledgement wait ever gets, so one bajutsu cannot read has to fail visibly (the handler
        answers 400) instead of leaving the wait to time out as though the app had stayed silent.
        Requiring the id to be one this run issued is the same guarantee against a stale report
        straggling in across a `clear()` and releasing the next scenario's wait.

        Neither refusal is recorded anywhere else, so the wait a refused report was meant for still
        fails by timing out rather than by naming the report bajutsu turned away.
        """
        try:
            report = AppCommandReport.model_validate(data)
        except ValidationError:
            return False
        with self._lock:
            if report.id not in self._issued:
                return False
            self._reports[report.id] = report
            return True

    def report_for(self, command_id: str) -> AppCommandReport | None:
        """The app's report on this command, or None while none has arrived.

        Three answers, none of them collapsed into another: None keeps the acknowledgement wait
        waiting, `applied=False` fails it at once with the app's own `reason`, and `applied=True`
        releases it (BE-0365 unit 3).
        """
        with self._lock:
            return self._reports.get(command_id)

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
        """Drop everything scoped to one scenario — exchanges, transitions, and channel state."""
        with self._lock:
            self._items.clear()
            self._transitions.clear()
            # The control channel is scenario-scoped for the same reason (BE-0365): a command one
            # scenario left undrained must not reach the next, and its acknowledgement must not
            # release a later wait. `_issued_count` survives on purpose — see `__init__`.
            self._commands.clear()
            self._issued.clear()
            self._reports.clear()

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

    def start_bridgeable(self) -> int:
        """Start the receiver on a port a leased device can mirror, for the `adb reverse` bridge.

        `start()`'s OS-chosen port is host-local, but the adb backend tunnels the collector with
        `adb reverse tcp:<port> tcp:<port>` (BE-0283) — the *emulator* has to bind the same number.
        An OS-chosen port comes from the host's ephemeral range, which is the guest's as well
        (32768-60999 on Linux), so it lands where the emulator is already handing ports out to its
        own sockets; when the guest happens to hold that one, adbd's bind fails and the bridge dies
        with `cannot bind listener: Address already in use`, taking the lease with it. Any guest
        socket collides, not just a listener — an outbound connection, or one left in `TIME_WAIT`.

        Preferring a band below both ephemeral ranges removes that collision class rather than
        narrowing it: nothing on either side allocates these ports by chance, so the number is free
        on the device precisely because it was free on the host.

        Returns:
            The bound port, as `start()` does.

        Raises:
            OSError: no reserved-band port was bindable. An occupancy error (EADDRINUSE) on one
                port advances to the next; any other bind error, and exhausting the whole band,
                raise rather than fall back to an OS-chosen ephemeral port — that fallback would
                sit in the shared range and reopen the guest-side collision this method removes.
        """
        for port in range(_BRIDGE_PORT_BASE, _BRIDGE_PORT_BASE + _BRIDGE_PORT_SPAN):
            try:
                return self.start(port)
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    # Not "port taken" (EACCES, EADDRNOTAVAIL, loopback down) — the next port
                    # will not fix it, so surface it rather than burn the whole band masking it
                    # as occupancy.
                    raise
                continue  # taken on this host (a parallel lane's collector); try the next
        # The band is the collision-free guarantee: an OS-chosen fallback would land in the shared
        # ephemeral range and reopen the very `adb reverse` collision this exists to remove. With
        # one collector per leased device, exhausting the whole reserved band means the host is
        # misconfigured — fail loudly instead of degrading to the flaky path.
        raise OSError(
            f"no free port in the reserved bridge band "
            f"{_BRIDGE_PORT_BASE}-{_BRIDGE_PORT_BASE + _BRIDGE_PORT_SPAN - 1}"
        )

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


# The receiver's known endpoints. Everything else POSTed is stored as a network exchange, which is
# why each of these has to be matched *before* that catch-all (BE-0365).
_TRANSITIONS_PATH = "/transitions"
_COMMANDS_PATH = "/commands"
_ACKNOWLEDGE_PATH = "/commands/ack"


def _make_handler(collector: NetworkCollector) -> type[BaseHTTPRequestHandler]:
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
            instead of falling through to the catch-all and being stored as a bogus exchange.
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
                [command.model_dump(mode="json") for command in collector.drain_commands()]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler
