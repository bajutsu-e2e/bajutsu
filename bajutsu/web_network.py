"""Web network observation — the Playwright-side collector and mock stubbing.

The iOS path ([`network.py`](network.py)) observes traffic out-of-process: the app POSTs each
exchange to an HTTP receiver. The web has it natively — Playwright sees every request the page
makes — so this collector hooks the page's `requestfinished` event into the *same*
`NetworkExchange` model and satisfies the same `Collector` protocol, so the `request` assertion,
evidence writer, and run loop drive web traffic unchanged.

Mocks (`scenario.mocks`) are fulfilled in-process via `page.route`: a matching outgoing request
gets the canned response (no live server) instead of going out, and is recorded with
`mocked=True`. Matching reuses the deterministic request matcher, so a mock matches exactly what
a `request` assertion would. No model is consulted anywhere — prime directive #1 holds.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from bajutsu.assertions import match_request
from bajutsu.network import NetworkExchange

if TYPE_CHECKING:
    from bajutsu.scenario.models.mocks import Mock


class WebNetworkCollector:
    """Collects the page's network exchanges and fulfills mocks, satisfying `Collector`.

    Holds each exchange with the monotonic time it completed (like `NetworkCollector`), so the
    report can place it on the scenario timeline. Unlike the iOS collector — whose HTTP-server
    thread races the run loop and so needs a lock — Playwright's sync events fire on the run
    thread, so there is no concurrent reader and no lock is needed.
    """

    def __init__(
        self, page: Any, mocks: list[Mock] | None = None, now: Callable[[], float] = time.monotonic
    ) -> None:
        self._items: list[tuple[NetworkExchange, float]] = []
        self._now = now
        self._mocks = mocks or []
        # Requests fulfilled from a mock, by identity, so `requestfinished` records them as mocked.
        self._mocked: set[int] = set()
        page.on("requestfinished", self._on_finished)
        if self._mocks:
            page.route("**/*", self._on_route)

    # --- Collector protocol ---

    def snapshot(self) -> list[NetworkExchange]:
        return [ex for ex, _ in self._items]

    def snapshot_timed(self) -> list[tuple[NetworkExchange, float]]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._mocked.clear()

    def stop(self) -> None:
        # Handlers live on the page; closing the driver disposes it, so there is nothing to tear
        # down here. Defined to satisfy the protocol (the iOS collector stops an HTTP server).
        pass

    # --- Playwright event handlers ---

    def _on_finished(self, request: Any) -> None:
        response = request.response()
        exchange = NetworkExchange(
            **_request_fields(request),
            status=response.status if response is not None else None,
            responseHeaders=dict(response.headers or {}) if response is not None else {},
            responseBody=_body(response),
            mocked=id(request) in self._mocked,
        )
        self._items.append((exchange, self._now()))

    def _on_route(self, route: Any) -> None:
        request = route.request
        # A mock matches the request exactly as a `request` assertion would (request-side only:
        # a `mocked` exchange gets its response from the mock once it finishes, below).
        probe = NetworkExchange(**_request_fields(request))
        for mock in self._mocks:
            if match_request(probe, mock.match):
                self._mocked.add(id(request))
                route.fulfill(
                    status=mock.respond.status,
                    headers=mock.respond.headers,
                    body=mock.respond.body or "",
                )
                return
        route.continue_()


def _request_fields(request: Any) -> dict[str, Any]:
    """The request-side `NetworkExchange` fields, shared by observation and mock matching."""
    return {
        "method": request.method,
        "url": request.url,
        "path": urlparse(request.url).path,
        "requestHeaders": dict(request.headers or {}),
        "requestBody": request.post_data,
    }


def _body(response: Any) -> str | None:
    """The response body as text, best-effort (a body may be unavailable or binary)."""
    if response is None:
        return None
    try:
        return str(response.text())
    except Exception:
        return None
