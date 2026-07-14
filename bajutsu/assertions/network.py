"""Network-timeline matching for assertions.

The request matcher (`match_request` / `count_matching`) is shared beyond assertions — the web
mock router and the `until: {request}` wait both depend on it — so a mock stubs exactly what an
assertion would match. `_assign_requests` is the one-to-one bipartite assignment that keeps a
broad `request` matcher from stealing the only exchange a more specific one needs.
"""

from __future__ import annotations

from bajutsu.assertions._common import AssertionResult, _compile
from bajutsu.network import NetworkExchange
from bajutsu.scenario import RequestMatch


def match_request(ex: NetworkExchange, req: RequestMatch) -> bool:
    """Whether one observed exchange satisfies a request matcher.

    Shared by the `request` assertion and the web mock router, so a mock stubs exactly what an
    assertion would match.

    Args:
        ex: One observed network exchange.
        req: The matcher; only its set (non-`None`) fields are checked, AND-ed together.

    Returns:
        True iff every set field of `req` matches `ex`.
    """
    # Straight-line early returns, kept allocation-free on purpose: this runs in `until: {request}`
    # polling and per-exchange matching loops, so it must stay lightweight (no per-call closures).
    if req.method is not None and ex.method.upper() != req.method.upper():
        return False
    if req.url is not None and ex.url != req.url:
        return False
    if req.url_matches is not None and _compile(req.url_matches).search(ex.url) is None:
        return False
    if req.path is not None and ex.path != req.path:
        return False
    if req.path_matches is not None and _compile(req.path_matches).search(ex.path) is None:
        return False
    if req.status is not None and ex.status != req.status:
        return False
    return not (
        req.body_matches is not None
        and (ex.request_body is None or _compile(req.body_matches).search(ex.request_body) is None)
    )


def count_matching(exchanges: list[NetworkExchange], req: RequestMatch) -> int:
    """How many observed exchanges satisfy the request matcher.

    Shared by the `request` assertion and the `until: { request }` wait.
    """
    return sum(1 for ex in exchanges if match_request(ex, req))


def request_label(req: RequestMatch, *, with_count: bool = True) -> str:
    """A compact human description of a request matcher (e.g. ``GET /items status=200``).

    Args:
        req: The request matcher to describe.
        with_count: When False, the matcher's `count` is left out of the label — used where `count`
            is not part of the check (e.g. `requestSequence`, which is about order), so the label
            doesn't imply a field that is ignored.

    Returns:
        The matcher's set fields joined into one space-separated line.
    """
    parts: list[str] = []
    if req.method is not None:
        parts.append(req.method.upper())
    if req.url is not None:
        parts.append(req.url)
    if req.url_matches is not None:
        parts.append(f"url~{req.url_matches}")
    if req.path is not None:
        parts.append(req.path)
    if req.path_matches is not None:
        parts.append(f"~{req.path_matches}")
    if req.status is not None:
        parts.append(f"status={req.status}")
    if req.body_matches is not None:
        parts.append(f"body~{req.body_matches}")
    if with_count and req.count is not None:
        parts.append(f"count={req.count}")
    return " ".join(parts)


def _assign_requests(exchanges: list[NetworkExchange], reqs: list[RequestMatch]) -> list[int]:
    """Assign each request matcher a *distinct* exchange — one `request` ↔ one exchange.

    Maximum bipartite matching (Kuhn's augmenting paths) so a broad matcher never steals
    the only exchange a more specific one needs. Returns, per matcher, the exchange index
    it was assigned, or -1 when none is left for it.
    """
    adj = [[j for j, ex in enumerate(exchanges) if match_request(ex, req)] for req in reqs]
    ex_to_req = [-1] * len(exchanges)
    assigned = [-1] * len(reqs)

    def augment(i: int, seen: list[bool]) -> bool:
        for j in adj[i]:
            if not seen[j]:
                seen[j] = True
                if ex_to_req[j] == -1 or augment(ex_to_req[j], seen):
                    ex_to_req[j], assigned[i] = i, j
                    return True
        return False

    for i in range(len(reqs)):
        augment(i, [False] * len(exchanges))
    return assigned


def _request_assignment_result(
    req: RequestMatch, assigned_ex: int, exchanges: list[NetworkExchange]
) -> AssertionResult:
    detail = f"request {request_label(req)}"
    if assigned_ex != -1:
        return AssertionResult(True, "request", detail)
    matched_any = any(match_request(ex, req) for ex in exchanges)
    reason = (
        "matching exchange already taken by another request (request ↔ exchange is one-to-one)"
        if matched_any
        else f"no matching exchange (observed {len(exchanges)})"
    )
    return AssertionResult(False, "request", detail, reason)
