"""Network observation — the exchange model and the in-process collector.

How traffic is observed (DESIGN: network): a Simulator app runs as a host process
and shares the Mac's loopback, so the app POSTs each request/response it makes to a
small collector bajutsu runs on `127.0.0.1:<port>` (the port is injected into the app
via launch env, `BAJUTSU_COLLECTOR`). The collector keeps the exchanges in memory so
a step's `request` assertion can be evaluated in real time, and dumps them to
`network.json` as scenario evidence.

The in-app side that captures and POSTs the exchanges is a separate Swift package
(`BajutsuKit`); this module is only the bajutsu-side receiver and data model.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NetworkExchange(BaseModel):
    """One request/response the app reported. Extra keys from the SDK are ignored
    (forward-compatible); field names accept their JSON aliases."""

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
        self._now = now
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    # --- data ---

    def add(self, data: dict[str, Any]) -> None:
        try:
            ex = NetworkExchange.model_validate(data)
        except Exception:
            return
        with self._lock:
            self._items.append((ex, self._now()))

    def snapshot(self) -> list[NetworkExchange]:
        with self._lock:
            return [ex for ex, _ in self._items]

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        """Each exchange with its receive time (monotonic), in arrival order."""
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # --- lifecycle ---

    def start(self, port: int = 0) -> int:
        """Start the receiver on 127.0.0.1:port (0 = an ephemeral port). Returns the
        bound port."""
        server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(self))
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _make_handler(collector: NetworkCollector) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            # Accept a single exchange or a batch (list).
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict):
                    collector.add(item)
            self.send_response(204)
            self.end_headers()

        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: Any) -> None:  # silence per-request stderr logging
            pass

    return Handler
