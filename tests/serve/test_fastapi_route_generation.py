"""The FastAPI backend's routes are generated from the shared route registry (BE-0253 Part 3).

`make_app` iterates `ROUTES` and registers every entry that carries a `handle` and is not
`local_only`, so the hosted backend's route table is derived from the same source of truth the
stdlib handler dispatches from. These tests lock that the generated surface matches the registry
(the anti-drift guard the item exists to provide — the FastAPI twin of the stdlib
`test_off_loop_routes_are_intercepted_before_registry`), that the Part 4 `local_only` triage really
keeps the local-only routes off this backend, that the endpoints previously missing here are now
served, and that a generated route decodes a percent-encoded path param exactly once. No mocks — a
real `ServeState` with local seams and a real project registry.
"""

from __future__ import annotations

from pathlib import Path

from _shared import project, write_run
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from bajutsu import serve as srv
from bajutsu.serve.handler import _StdlibCtx
from bajutsu.serve.project_registry import LocalProjectRegistry
from bajutsu.serve.routes import ROUTES
from bajutsu.serve.server.app import _FastapiCtx, make_app


def _state(tmp_path: Path, **kw: object) -> srv.ServeState:
    reg = LocalProjectRegistry(tmp_path / "projects.json")
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir,
        config=cfg,
        runs_dir=runs,
        root=tmp_path,
        cwd=tmp_path,
        project_registry=reg,
        **kw,  # type: ignore[arg-type]
    )


def _exposed(app: object) -> set[tuple[str, str]]:
    """Every (method, path) the FastAPI app actually serves — the generated routes plus the bespoke
    off_loop handlers. Starlette adds HEAD to GET routes, so a per-method flatten is fine; the
    assertions below only ever test membership of a specific (method, path)."""
    return {
        (method, route.path)
        for route in app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_fastapi_app_exposes_every_non_local_only_registry_route(tmp_path: Path) -> None:
    # The generated (uniform/text) routes and the bespoke off_loop ones together must cover every
    # non-local_only entry in ROUTES. A route added to the registry without a FastAPI counterpart
    # (a generated handle, or a bespoke off_loop handler for handle=None) fails here.
    exposed = _exposed(make_app(_state(tmp_path)))
    missing = [
        (r.method, r.path) for r in ROUTES if not r.local_only and (r.method, r.path) not in exposed
    ]
    assert missing == []


def test_fastapi_app_skips_local_only_registry_routes(tmp_path: Path) -> None:
    # Part 4 triage: the hosted backend deliberately does not serve the local-only routes.
    exposed = _exposed(make_app(_state(tmp_path)))
    leaked = [(r.method, r.path) for r in ROUTES if r.local_only and (r.method, r.path) in exposed]
    assert leaked == []


def test_local_only_routes_are_not_found_on_the_hosted_backend(tmp_path: Path) -> None:
    client = TestClient(make_app(_state(tmp_path)))
    assert client.get("/api/ant/login").status_code == 404
    assert client.post("/api/ant/login").status_code == 404
    assert client.post("/api/capture/start", json={}).status_code == 404
    assert client.get("/api/capture/screenshot").status_code == 404


def test_backfilled_flakiness_route_serves_html(tmp_path: Path) -> None:
    # Present in the stdlib handler but missing from app.py until PR-C; now generated as a text route.
    state = _state(tmp_path)
    write_run(tmp_path / "runs", "20260101-000000", ok=True, scenarios=[("alpha", True)])
    resp = TestClient(make_app(state)).get("/flakiness")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_backfilled_author_edit_routes_delegate_to_ops(tmp_path: Path) -> None:
    # apply-selector / enrich-apply were never mirrored into app.py (a silent drift the registry
    # closes). An empty body reaches each op's own validation 400 — proving the route is wired to the
    # op, not the router's absent-route 404.
    client = TestClient(make_app(_state(tmp_path)))
    sel = client.post("/api/scenario/apply-selector", json={})
    assert sel.status_code == 400 and sel.json() == {"error": "selector must be a non-empty object"}
    enr = client.post("/api/scenario/enrich-apply", json={})
    assert enr.status_code == 400 and enr.json() == {"error": "expect must be a list of assertions"}


def test_backfilled_respond_human_route_delegates_to_ops(tmp_path: Path) -> None:
    # An unknown job id reaches respond_human's own 404 ({"error": "no such job"}), distinct from the
    # FastAPI router's {"detail": "Not Found"} — so the route exists and dispatches to the op.
    resp = TestClient(make_app(_state(tmp_path))).post("/api/jobs/nosuchjob/respond-human", json={})
    assert resp.status_code == 404
    assert resp.json() == {"error": "no such job"}


# --- path_param decodes exactly once, symmetrically on both backends (BE-0253 PR-C) ---
#
# The registry closures no longer call `unquote` themselves; each ctx returns the decoded segment.
# The stdlib matcher runs on the raw (encoded) request path, so its ctx unquotes once; Starlette
# hands the FastAPI ctx an already-decoded param, so its ctx passes it straight through. A second
# `unquote` in either ctx (or a closure) would turn a literal "%20" in a name into a space. The two
# ctxs are unit-tested here rather than end-to-end because Starlette's TestClient over-decodes the
# path itself, which would mask the contract these pin.


def test_stdlib_ctx_decodes_a_path_param_exactly_once() -> None:
    # The matcher binds the raw encoded segment "a%2520b"; the ctx unquotes it exactly once.
    ctx = _StdlibCtx({"name": "a%2520b"}, {}, lambda _key: None, lambda: None)
    assert ctx.path_param("name") == "a%20b"


def test_fastapi_ctx_returns_the_starlette_decoded_param_without_re_decoding() -> None:
    # Starlette already decoded "a%2520b" down to "a%20b" once; the ctx must not unquote again (that
    # would give "a b"), so both backends deliver the same decoded value to a closure.
    request = Request({"type": "http", "path_params": {"name": "a%20b"}, "query_string": b""})
    ctx = _FastapiCtx(request, {}, lambda: None)
    assert ctx.path_param("name") == "a%20b"
