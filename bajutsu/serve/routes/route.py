"""One endpoint's declaration, shared by both server backends (BE-0253)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bajutsu.serve.state import ServeState

from .request_ctx import RequestCtx

# A uniform route's adapter: extract this route's arguments from the request and call its `ops`
# function, returning the `(payload, status)` pair the backend writes as JSON (or text).
Handle = Callable[[ServeState, RequestCtx], tuple[Any, int]]

# The raw-byte bound on a JSON POST body, enforced before the body is buffered (BE-0449). The
# largest bodies a client legitimately sends are a scenario's YAML (`/api/scenario`, a file the
# upload path caps at 2 MiB) and a worker's run result. Even a 2 MiB scenario whose every character
# JSON escapes to `\uXXXX` stays under 12 MiB, so this leaves headroom while still refusing a body
# that could only exhaust memory.
MAX_JSON_BODY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Route:
    """One endpoint's declaration, shared by both backends (BE-0253).

    Attributes:
        method: HTTP method — "GET", "POST", or "DELETE".
        path: FastAPI-style path template (e.g. "/api/orgs/{slug}/membership").
        handle: The uniform `(state, ctx) -> (payload, status)` adapter, or None for an
            `off_loop` route each backend handles bespoke.
        off_loop: The route writes its own response (streaming, file serve, raw upload, redirect)
            rather than the uniform JSON/text path; declared here but dispatched per backend.
        local_only: The route needs a single-process server (Part 4's triage: `/api/ant/login`,
            `/api/capture/*`), so the FastAPI app skips it on a hosted deployment (BE-0449).
        content_type: When set, the response is text of this type instead of JSON.
        max_body: The most bytes a POST body may carry; a larger one is refused with 413 before
            it is read whole.
    """

    method: str
    path: str
    handle: Handle | None = None
    off_loop: bool = False
    local_only: bool = False
    content_type: str | None = None
    max_body: int = MAX_JSON_BODY_BYTES
