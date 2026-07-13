"""Tests for the web (Playwright) network collector — browser-free via a fake page.

The collector turns Playwright request/response events into the same `NetworkExchange` the
`request` assertion already consumes on iOS, and fulfills scenario mocks via `page.route`. It
is exercised through a fake page that records handlers and lets the test fire events, so the
whole module is covered without launching Chromium (the same style as test_playwright).
"""

from __future__ import annotations

from typing import Any

from bajutsu.assertions import evaluate_one
from bajutsu.scenario.models.assertions import Assertion, RequestMatch
from bajutsu.scenario.models.mocks import Mock, MockResponse
from bajutsu.web_network import WebNetworkCollector


class _FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body: str = "") -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body

    def text(self) -> str:
        return self._body


class _FakeRequest:
    def __init__(
        self,
        method: str,
        url: str,
        *,
        post_data: str | None = None,
        headers: dict[str, str] | None = None,
        response: _FakeResponse | None = None,
    ) -> None:
        self.method = method
        self.url = url
        self.post_data = post_data
        self.headers = headers or {}
        self._response = response

    def response(self) -> _FakeResponse | None:
        return self._response


class _FakeRoute:
    def __init__(self, request: _FakeRequest) -> None:
        self.request = request
        self.fulfilled: dict[str, Any] | None = None
        self.continued = False

    def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled = kwargs

    def continue_(self) -> None:
        self.continued = True


class _FakePage:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Any]] = {}
        self._routes: list[Any] = []

    def on(self, event: str, handler: Any) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def route(self, pattern: str, handler: Any) -> None:
        self._routes.append(handler)

    # --- test drivers (simulate Playwright firing events) ---

    def finish(self, request: _FakeRequest) -> None:
        for handler in self._handlers.get("requestfinished", []):
            handler(request)

    def route_request(self, route: _FakeRoute) -> None:
        for handler in self._routes:
            handler(route)


def test_records_a_completed_request_as_an_exchange() -> None:
    page = _FakePage()
    collector = WebNetworkCollector(page)
    page.finish(
        _FakeRequest(
            "GET",
            "https://api.test/items?q=1",
            headers={"Accept": "application/json"},
            response=_FakeResponse(200, {"Content-Type": "application/json"}, body="[]"),
        )
    )
    [ex] = collector.snapshot()
    assert ex.method == "GET"
    assert ex.url == "https://api.test/items?q=1"
    assert ex.path == "/items"  # query stripped, for matching
    assert ex.status == 200
    assert ex.mocked is False


def test_late_requestfinished_after_target_closed_is_dropped() -> None:
    # A `requestfinished` can fire while the page navigates or after the context/browser closes;
    # `response()` then raises (TargetClosedError). The handler must swallow it and record nothing,
    # rather than let it surface as an unhandled error on Playwright's event loop.
    page = _FakePage()
    collector = WebNetworkCollector(page)

    class _ClosedRequest(_FakeRequest):
        def response(self) -> _FakeResponse | None:
            raise RuntimeError("Request.response: Target page, context or browser has been closed")

    page.finish(_ClosedRequest("GET", "https://api.test/late"))
    assert collector.snapshot() == []


def test_snapshot_timed_and_clear() -> None:
    page = _FakePage()
    collector = WebNetworkCollector(page)
    page.finish(_FakeRequest("GET", "https://api.test/a", response=_FakeResponse(200)))
    assert len(collector.snapshot_timed()) == 1
    assert all(isinstance(t, float) for _, t in collector.snapshot_timed())
    collector.clear()
    assert collector.snapshot() == []


def test_mock_fulfills_a_matching_request_and_marks_it_mocked() -> None:
    page = _FakePage()
    mock = Mock(
        match=RequestMatch(method="POST", url_matches="api.test/login"),
        respond=MockResponse(status=418, headers={"X-T": "1"}, body="no"),
    )
    collector = WebNetworkCollector(page, mocks=[mock])
    route = _FakeRoute(_FakeRequest("POST", "https://api.test/login", post_data="{}"))
    page.route_request(route)
    assert route.fulfilled is not None
    assert route.fulfilled["status"] == 418
    assert route.continued is False
    # the fulfilled response then completes; the recorded exchange is flagged mocked
    route.request._response = _FakeResponse(418, {"X-T": "1"}, body="no")
    page.finish(route.request)
    [ex] = collector.snapshot()
    assert ex.mocked is True
    assert ex.status == 418


def test_mock_lets_an_unmatched_request_continue() -> None:
    page = _FakePage()
    mock = Mock(match=RequestMatch(path="/login"), respond=MockResponse(status=200))
    collector = WebNetworkCollector(page, mocks=[mock])
    route = _FakeRoute(_FakeRequest("GET", "https://api.test/items"))
    page.route_request(route)
    assert route.continued is True
    assert route.fulfilled is None
    assert collector.snapshot() == []  # a continued request isn't recorded until it finishes


def test_mock_ignores_status_in_the_matcher_like_ios() -> None:
    # `status` does not apply to mock matching (parity with iOS BajutsuKit): a mock that pins a
    # response `status` still matches on its request-side path, rather than never matching because
    # the outgoing request has no status yet.
    page = _FakePage()
    mock = Mock(
        match=RequestMatch(path="/login", status=200),
        respond=MockResponse(status=201),
    )
    WebNetworkCollector(page, mocks=[mock])
    route = _FakeRoute(_FakeRequest("GET", "https://api.test/login"))
    page.route_request(route)
    assert route.fulfilled is not None and route.fulfilled["status"] == 201


def test_mock_honors_delay_ms_before_fulfilling() -> None:
    # delayMs is an author-requested latency injection (implemented on iOS); web honors it too.
    # The sleep is injected so the test asserts the delay without actually waiting.
    sleeps: list[float] = []
    page = _FakePage()
    mock = Mock(match=RequestMatch(path="/slow"), respond=MockResponse(delay_ms=50.0))
    WebNetworkCollector(page, mocks=[mock], sleep=sleeps.append)
    route = _FakeRoute(_FakeRequest("GET", "https://api.test/slow"))
    page.route_request(route)
    assert route.fulfilled is not None
    assert sleeps == [0.05]  # 50 ms, not a synchronization wait


def test_request_assertion_passes_over_web_collected_exchanges() -> None:
    # The deterministic `request` assertion runs unchanged over the web collector's snapshot.
    page = _FakePage()
    collector = WebNetworkCollector(page)
    page.finish(_FakeRequest("POST", "https://api.test/login", response=_FakeResponse(200)))
    assertion = Assertion(request=RequestMatch(method="POST", path_matches="login", count=1))
    result = evaluate_one([], assertion, collector.snapshot())
    assert result.ok is True
