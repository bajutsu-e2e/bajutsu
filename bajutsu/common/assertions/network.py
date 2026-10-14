"""Network-timeline matching for assertions.

The request matcher (`match_request` / `count_matching`) is shared beyond assertions — the web
mock router and the `until: {request}` wait both depend on it — so a mock stubs exactly what an
assertion would match. `_assign_requests` is the one-to-one bipartite assignment that keeps a
broad `request` matcher from stealing the only exchange a more specific one needs (BE-0250).
"""

from __future__ import annotations

import json

from bajutsu.common.assertions._common import AssertionResult, _compile
from bajutsu.common.assertions.dict_match import dict_mismatch
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.scenario import RequestMatch
from bajutsu.common.scenario.models.assertions import _EndpointMatch


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
    # Only a set `query` / `body` reaches `dict_mismatch`, which formats text on a mismatch alone.
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
    if req.body_matches is not None and (
        ex.request_body is None or _compile(req.body_matches).search(ex.request_body) is None
    ):
        return False
    return (req.query is None and req.body is None) or dict_mismatch(
        ex, req.query, req.body
    ) is None


def dictionary_hint(exchanges: list[NetworkExchange], m: _EndpointMatch) -> str | None:
    """The mismatched `query` / `body` key behind a failed match, when one candidate pins it.

    Only when exactly one exchange satisfies the endpoint and method criteria: with several, a
    guess at which one the author meant would mislead more than the plain count does (BE-0463).

    Returns:
        The first failing key (e.g. ``query.page: expected "2", got "3"``), or None when there is
        no dictionary, no single candidate, or the dictionaries all hold on it (the failure then
        lies in another criterion).
    """
    if m.query is None and m.body is None:
        return None
    endpoint = RequestMatch.model_construct(
        method=m.method,
        url=m.url,
        url_matches=m.url_matches,
        path=m.path,
        path_matches=m.path_matches,
    )
    candidates = [ex for ex in exchanges if match_request(ex, endpoint)]
    if len(candidates) != 1:
        return None
    return dict_mismatch(candidates[0], m.query, m.body)


def dict_label(name: str, matcher: dict[str, object]) -> str:
    """One dictionary criterion as compact JSON (``query={"page":"2"}``), for labels and reports."""
    return f"{name}={json.dumps(matcher, separators=(',', ':'), ensure_ascii=False)}"


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
    if req.query is not None:
        parts.append(dict_label("query", req.query))
    if req.body is not None:
        parts.append(dict_label("body", req.body))
    if req.status is not None:
        parts.append(f"status={req.status}")
    if req.body_matches is not None:
        parts.append(f"body~{req.body_matches}")
    if with_count and req.count is not None:
        parts.append(f"count={req.count}")
    return " ".join(parts)


def _assign_requests(
    exchanges: list[NetworkExchange], reqs: list[RequestMatch]
) -> tuple[list[int], list[bool]]:
    """Assign each request matcher a *distinct* exchange — one `request` ↔ one exchange.

    Maximum bipartite matching (Kuhn's augmenting paths) so a broad matcher never steals
    the only exchange a more specific one needs.

    Returns:
        A pair of per-matcher lists, both positionally aligned with `reqs`: the exchange index
        each matcher was assigned (-1 when none is left for it), and whether it matched *any*
        exchange at all (its adjacency was non-empty). The second list lets the caller explain an
        unassigned matcher without re-scanning every exchange — it is exactly the `matched_any`
        a re-scan would recompute.
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
    return assigned, [bool(candidates) for candidates in adj]


def _request_assignment_result(
    req: RequestMatch, assigned_ex: int, exchanges: list[NetworkExchange], *, matched_any: bool
) -> AssertionResult:
    detail = f"request {request_label(req)}"
    if assigned_ex != -1:
        return AssertionResult(True, "request", detail)
    if matched_any:
        reason = (
            "matching exchange already taken by another request (request ↔ exchange is one-to-one)"
        )
    else:
        reason = (
            dictionary_hint(exchanges, req) or f"no matching exchange (observed {len(exchanges)})"
        )
    return AssertionResult(False, "request", detail, reason)
