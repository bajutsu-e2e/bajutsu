"""The declarative route registry both serve backends dispatch from (BE-0253).

`serve` runs two HTTP backends behind one API surface — a stdlib `http.server` handler
(`handler.py`) and a FastAPI app (`server/app.py`). Historically each declared every endpoint
twice: once as a `match path` case in the stdlib handler and once as an `@app.<method>` route,
both dispatching to the same `bajutsu.serve.operations`. The two hand-maintained tables drifted
silently (endpoints present in one backend, missing in the other).

This module is the single source of truth: one `ROUTES` table each backend iterates. An entry
carries the HTTP method, the path pattern (FastAPI-style templates like `/api/jobs/{job_id}` —
the spelling FastAPI consumes directly), and, for the *uniform* routes, a backend-neutral
`handle(state, ctx)` adapter that captures the `ops.*` call once. Each backend supplies its own
`ctx` (how a request's query/body/path-params/actor are read) and its own response writer, so the
transport mechanics stay per-backend while the route table is shared.

`off_loop` routes (SSE, file/range serving, raw-body uploads, the OAuth round-trip, login, and the
index render) write their own responses and differ structurally per backend, so the registry only
*declares* them (`handle=None`) — each backend keeps its bespoke handling. `local_only` marks a
route the FastAPI generator deliberately skips — Part 4's triage marks `/api/ant/login` and
`/api/capture/*`; every other route is served by both backends. `content_type`, when set, selects a
text response over JSON.

Framework-agnostic by construction — like `gate.py`, it must import without FastAPI so the default
stdlib serve path stays lean (`tests/serve/test_import_guard.py`).
"""

from ._functions import _match_template as _match_template
from ._functions import json_body_cap, match_route
from ._shared import _HTML as _HTML
from ._shared import ROUTES
from .request_ctx import RequestCtx
from .route import MAX_JSON_BODY_BYTES, Handle, Route

__all__ = [
    "MAX_JSON_BODY_BYTES",
    "ROUTES",
    "Handle",
    "RequestCtx",
    "Route",
    "json_body_cap",
    "match_route",
]
