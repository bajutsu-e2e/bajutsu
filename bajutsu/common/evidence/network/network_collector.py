"""The collector receiving exchanges the app posts, holding them for assertions and evidence."""

from __future__ import annotations

import errno
import ipaddress
import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable, Sequence
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ._functions import _make_handler
from .app_command import AppCommand
from .app_command_report import AppCommandReport
from .app_stub_table_command import AppStubTableCommand, PendingCommand
from .in_app_capability import ToggleCapability
from .network_exchange import NetworkExchange
from .screen_transition import ScreenTransition

if TYPE_CHECKING:
    from bajutsu.common.scenario.models.mocks import Mock

# The band `start_bridgeable` draws the collector's port from — below both the host's and the
# emulator's ephemeral ranges, and beside the resident UI Automator server's device port
# (`adb.RESIDENT_DEVICE_PORT`, 6790), which reserves a fixed port for the same reason. The span is
# the ceiling on collectors sharing one host: one per leased device, so it covers every parallel
# lane a run can hold and every other bajutsu process on the machine.
_BRIDGE_PORT_BASE = 6800
_BRIDGE_PORT_SPAN = 100

_logger = logging.getLogger(__name__)


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
        self._commands: list[PendingCommand] = []
        self._issued: set[str] = set()
        self._reports: dict[str, AppCommandReport] = {}
        # Monotonic for this collector's whole life and deliberately *not* reset by `clear()`: a
        # reused id would let a cleared scenario's late acknowledgement match a fresh command and
        # release its wait without the app having applied anything.
        self._issued_count = 0
        # The signed scheme's replay guard (BE-0459): every nonce whose signature matched, for the
        # collector's whole life. `clear()` keeps it, so a request captured in one scenario stays
        # refused in the next.
        self._nonces: set[str] = set()
        # The bearer header is accepted on the loopback alone; `start()` narrows this for a binding
        # that reaches beyond it, and counts the refused bearers that carried this run's token.
        self._bearer_accepted = True
        self._refused_bearers = 0

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

    def enqueue_command(self, capability: ToggleCapability, *, enabled: bool) -> str:
        """Queue one command for the app to drain, and return the id that identifies it.

        Args:
            capability: which piece of bajutsu's in-app instrumentation the command addresses.
            enabled: the state that capability should take.

        Returns:
            The command's id, to condition-wait on through `report_for` (BE-0365 unit 3).
        """
        with self._lock:
            command_id = self._next_id()
            self._commands.append(AppCommand(id=command_id, capability=capability, enabled=enabled))
            self._issued.add(command_id)
            return command_id

    def enqueue_stub_table(self, mocks: Sequence[Mock]) -> str:
        """Queue a replacement of the app's whole stub table, and return the id that identifies it.

        Args:
            mocks: the table the app should serve from now on, replacing the one it launched with;
                empty removes every stub.

        Returns:
            The command's id, to condition-wait on through `report_for` (BE-0365 unit 4).
        """
        with self._lock:
            command_id = self._next_id()
            self._commands.append(AppStubTableCommand(id=command_id, mocks=tuple(mocks)))
            self._issued.add(command_id)
            return command_id

    def _next_id(self) -> str:
        # Caller holds `_lock`; one counter across every command kind keeps ids unique run-wide.
        self._issued_count += 1
        return f"c{self._issued_count}"

    def drain_commands(self) -> list[PendingCommand]:
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

    def check_bearer(self, candidate: str) -> bool:
        """Whether a bearer header may authenticate: on the loopback binding alone (BE-0459).

        Beyond the loopback the token would cross the network in cleartext, so every bearer is
        refused there. One that carries this run's token is counted — an app built against a
        BajutsuKit that predates signing — and a made-up value is not, so a stranger reaching the
        listener cannot raise a false warning.
        """
        if self._bearer_accepted:
            return self.check_token(candidate)
        if self.check_token(candidate):
            with self._lock:
                self._refused_bearers += 1
                first = self._refused_bearers == 1
            if first:
                # Warned at the first refusal, not at teardown: the scenario failing on an empty
                # record is read while the run is still going, and this names its cause.
                _logger.warning(
                    "the network collector refused a bearer-token request carrying this run's "
                    "token: the app's BajutsuKit predates signed requests (BE-0459), so a "
                    "real-device run records no exchanges until the app is rebuilt against a "
                    "current BajutsuKit"
                )
        return False

    def nonce_spent(self, nonce: str) -> bool:
        """Whether a signed request with this nonce has already been accepted."""
        with self._lock:
            return nonce in self._nonces

    def claim_nonce(self, nonce: str) -> bool:
        """Record a verified request's nonce; False when another copy already claimed it.

        The check and the record are one step under the lock, so of two copies handled at once on
        the server's threads exactly one passes.
        """
        with self._lock:
            if nonce in self._nonces:
                return False
            self._nonces.add(nonce)
            return True

    @property
    def refused_bearer_count(self) -> int:
        """How many bearer headers with this run's token a non-loopback binding refused."""
        with self._lock:
            return self._refused_bearers

    @property
    def nonce_count(self) -> int:
        """How many signed requests this collector has accepted, one nonce each."""
        with self._lock:
            return len(self._nonces)

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

    def start(self, port: int = 0, *, host: str = "127.0.0.1") -> int:
        """Start the receiver and begin accepting the app's POSTs.

        Args:
            port: TCP port to bind; `0` requests an ephemeral port.
            host: The address to bind. The loopback, unless the app runs on a real device that does
                not share it; `"::"` then binds every IPv4 and IPv6 interface, which the per-run token
                every request must prove keeps closed to anything but this run's app. Beyond the
                loopback, a request signs with the token rather than carrying it (BE-0459).

        Returns:
            The actual bound port (resolved when `port` is `0`), to inject into the app via
            `BAJUTSU_COLLECTOR`.
        """
        self.token = secrets.token_urlsafe()
        self._bearer_accepted = _is_loopback(host)
        server = _bind(host, port, _make_handler(self))
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
            self._report_auth()  # inside the started guard, so a second stop() logs nothing
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join()  # serve_forever has returned; join so no stale thread lingers
            self._thread = None
        self.port = 0

    def _report_auth(self) -> None:
        """Log how many signed requests the run accepted — the size of its nonce set (BE-0459)."""
        _logger.info(
            "network collector accepted %d signed requests and refused %d bearer requests",
            self.nonce_count,
            self.refused_bearer_count,
        )


def _is_loopback(host: str) -> bool:
    """Whether binding `host` keeps the collector on this machine's loopback."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _bind(host: str, port: int, handler: Any) -> ThreadingHTTPServer:
    """The receiver's server on `host`; `::` falls back to every IPv4 interface without IPv6."""
    if host != "::":
        return ThreadingHTTPServer((host, port), handler)
    try:
        return _DualStackServer((host, port), handler)
    except OSError as exc:
        if exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
            raise
        return ThreadingHTTPServer(("0.0.0.0", port), handler)  # noqa: S104 — token-gated, see start()


class _DualStackServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` on `::` that also accepts IPv4, for a real device's collector."""

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        # Off by default on some hosts; a candidate list mixes both families, so accept both.
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()
