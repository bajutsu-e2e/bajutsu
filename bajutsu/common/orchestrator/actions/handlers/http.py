"""The `http` step: issue an HTTP request (test-data setup, webhook triggers, API calls) and
optionally save the response body (`saveBody`) to vars.*. With `timeout`, the request repeats until
the expected status (BE-0515)."""

from __future__ import annotations

from bajutsu.common.cancellation import CancelSource, RunCancelled, not_cancelled
from bajutsu.common.drivers import base
from bajutsu.common.orchestrator.actions._registry import _handler
from bajutsu.common.orchestrator.types import Clock, RealClock
from bajutsu.common.scenario import HttpRequest, Step

# The ceiling on one request, and how often a `timeout` wait repeats it. Like `email`, each repeat
# is a call to a remote service, so it runs about once a second rather than at the UI's poll rate.
_REQUEST_TIMEOUT = 30.0
_HTTP_POLL = 1.0


def _send(request: HttpRequest, timeout: float) -> tuple[int, str]:
    """Issue the request once and return the status and body, whatever the status is."""
    import urllib.error
    import urllib.request
    from http.client import HTTPException

    if not request.url.startswith(("http://", "https://")):
        raise base.SelectorError(f"http: only http/https URLs are allowed, got {request.url!r}")

    req = urllib.request.Request(  # noqa: S310 (scheme restricted to http/https above)
        request.url,
        data=request.body.encode("utf-8") if request.body else None,
        headers=dict(request.headers or {}),
        method=request.method,
    )
    # `OSError` / `HTTPException` as well as `URLError`: urllib wraps only a failure to send, so a
    # read timeout or a dropped connection would otherwise escape the step's own failure handling.
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (http/https only)
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise base.SelectorError(f"http: request failed: {e.reason}") from e
    except (OSError, HTTPException) as e:
        raise base.SelectorError(f"http: request failed: {e!r}") from e


def _store_body(http: HttpRequest, body: str, bindings: dict[str, str] | None) -> None:
    """Apply `saveBody` to the body of a response that passed the status check."""
    if http.save_body is not None and bindings is not None:
        bindings[f"vars.{http.save_body}"] = body


def _await_status(http: HttpRequest, timeout: float, clock: Clock, cancelled: CancelSource) -> str:
    """Repeat the request until the response carries `http.status`, and return that body (BE-0515).

    A condition wait bounded by `timeout`: another status leads to the next attempt, and so does a
    request that used up its own time. A request that errors sooner fails at once.

    Raises:
        base.SelectorError: The deadline passed, or a request got no response.
        RunCancelled: The run was cancelled mid-wait (BE-0370).
    """
    deadline = clock.now() + timeout
    last: int | None = None
    cut_off: base.SelectorError | None = None
    while True:
        if cancelled():
            raise RunCancelled
        remaining = deadline - clock.now()
        if remaining <= 0:
            break
        budget = min(remaining, _REQUEST_TIMEOUT)
        started = clock.now()
        try:
            status, body = _send(http, budget)
        except base.SelectorError as e:
            # Elapsed time tells a real error from a request its own time limit cut short.
            if clock.now() - started < budget:
                raise
            cut_off = e
            continue
        cut_off = None
        if status == http.status:
            return body
        last = status
        clock.sleep(max(0.0, min(_HTTP_POLL, deadline - clock.now())))
    seen = "no response arrived" if last is None else f"last status was {last}"
    # A cut-off last request may mean more than slowness, so its cause is kept.
    if cut_off is not None:
        seen += f"; last attempt: {cut_off}"
    raise base.SelectorError(
        f"http: no status {http.status} within {timeout:g}s; {seen}"
    ) from cut_off


def _do_http(
    http: HttpRequest,
    bindings: dict[str, str] | None,
    clock: Clock | None = None,
    cancelled: CancelSource = not_cancelled,
) -> None:
    """Execute an HTTP request and optionally save the response body to vars.*.

    `clock` and `cancelled` matter only under `http.timeout`: the run loop passes its own, while a
    caller with no run loop (the REPL) waits on the real clock.
    """
    if http.timeout is not None:
        body = _await_status(http, http.timeout, clock or RealClock(), cancelled)
    else:
        status, body = _send(http, _REQUEST_TIMEOUT)
        if http.status is not None and status != http.status:
            raise base.SelectorError(f"http: expected status {http.status}, got {status}")
    _store_body(http, body, bindings)


@_handler("http")
def _do_http_action(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.http is not None
    _do_http(step.http, bindings)
