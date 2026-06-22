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
    from bajutsu.scenario.models.assertions import RequestMatch
    from bajutsu.scenario.models.mocks import Mock


class WebNetworkCollector:
    """Collects the page's network exchanges and fulfills mocks, satisfying `Collector`.

    Holds each exchange with the monotonic time it completed (like `NetworkCollector`), so the
    report can place it on the scenario timeline. Unlike the iOS collector — whose HTTP-server
    thread races the run loop and so needs a lock — Playwright's sync events fire on the run
    thread, so there is no concurrent reader and no lock is needed.
    """

    def __init__(
        self,
        page: Any,
        mocks: list[Mock] | None = None,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._items: list[tuple[NetworkExchange, float]] = []
        self._now = now
        self._sleep = sleep
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
        probe = NetworkExchange(**_request_fields(request))
        for mock in self._mocks:
            if _mock_matches(probe, mock.match):
                self._mocked.add(id(request))
                if mock.respond.delay_ms:
                    # An author-requested latency injection (part of the mock DSL, like iOS), not a
                    # synchronization wait — so this fixed sleep does not violate determinism-first.
                    self._sleep(mock.respond.delay_ms / 1000.0)
                route.fulfill(
                    status=mock.respond.status,
                    headers=mock.respond.headers,
                    body=mock.respond.body or "",
                )
                return
        route.continue_()


def _mock_matches(probe: NetworkExchange, match: RequestMatch) -> bool:
    """Whether an outgoing request matches a mock, on its **request-side** fields only.

    `status` / `count` don't apply to mock matching (the iOS BajutsuKit matcher ignores them), so
    they are stripped first — otherwise a mock with `status` set would never match here (the probe
    has no status yet) and behave differently than on iOS. A matcher left with no request-side
    criterion would match every request, so it is treated as no-match instead."""
    request_side = match.model_copy(update={"status": None, "count": None})
    if not any(
        (
            request_side.method,
            request_side.url,
            request_side.url_matches,
            request_side.path,
            request_side.path_matches,
            request_side.body_matches,
        )
    ):
        return False
    return match_request(probe, request_side)


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
