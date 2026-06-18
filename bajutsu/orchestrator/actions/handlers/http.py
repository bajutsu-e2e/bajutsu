"""The `http` step: issue an HTTP request (test-data setup, webhook triggers, API calls) and
optionally save the response body to vars.*."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import HttpRequest, Step


def _do_http(http: HttpRequest, bindings: dict[str, str] | None) -> None:
    """Execute an HTTP request and optionally save the response body to vars.*."""
    import urllib.error
    import urllib.request

    if not http.url.startswith(("http://", "https://")):
        raise base.SelectorError(f"http: only http/https URLs are allowed, got {http.url!r}")

    req = urllib.request.Request(
        http.url,
        data=http.body.encode("utf-8") if http.body else None,
        headers=dict(http.headers or {}),
        method=http.method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        status = e.code
    except urllib.error.URLError as e:
        raise base.SelectorError(f"http: request failed: {e.reason}") from e
    if http.status is not None and status != http.status:
        raise base.SelectorError(f"http: expected status {http.status}, got {status}")
    if http.save_body is not None and bindings is not None:
        bindings[f"vars.{http.save_body}"] = body


@_handler("http")
def _do_http_action(
    _d: object, step: Step, _r: object, _c: object, bindings: dict[str, str] | None
) -> None:
    assert step.http is not None
    _do_http(step.http, bindings)
