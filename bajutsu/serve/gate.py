"""The shared request-gate policy both serve backends enforce (BE-0253).

`serve` runs two HTTP backends behind one API surface — a stdlib `http.server` handler
(`handler.py`) and a FastAPI app (`server/app.py`) — and each must apply the same security
posture: the authentication gate (BE-0051), the unconditional cross-origin (CSRF) `Origin`
check and the `Host` allowlist (BE-0121), and the hardening response headers. Previously each
backend reimplemented that posture independently, so the two copies had to be kept
byte-compatible by hand for the security enforcement to actually match — a latent path to a
security-relevant divergence with nothing checking for it.

This module owns those *policy decisions* once. It is deliberately small and pure: each backend
still owns its own *transport mechanics* — how it reads a request's headers/cookies and writes a
response, and where it drains a body or closes a connection — because those differ (sync stdlib
vs. async ASGI) and are mechanism, not policy. The gate is the shared judgment; the plumbing
around it stays local.

Framework-agnostic by construction — it must import without FastAPI so the default stdlib serve
path stays lean (`tests/serve/test_import_guard.py`).
"""

from __future__ import annotations

from urllib.parse import urlparse

from bajutsu.serve.sessions import Principal
from bajutsu.serve.state import SessionManager

# Standard hardening headers on every response (BE-0051): block MIME sniffing and cross-origin
# framing (clickjacking), and don't leak the URL via Referer. SAMEORIGIN (not DENY) so the Replay
# view can frame its own run report (/runs/<id>/report.html); cross-origin framing stays blocked.
HARDENING_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
}

# Host-header values that always name this machine (BE-0121 DNS-rebinding defense).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Endpoints reachable without a credential when a token is configured: the index page so the login
# UI can load, the OAuth round-trip, and the login endpoint itself.
_OPEN_GET_PATHS = ("/", "/index.html", "/api/oauth/login", "/api/oauth/callback")
_LOGIN_PATH = "/api/login"
OIDC_EXCHANGE_PATH = "/api/oidc/exchange"
_WORKER_PREFIX = "/api/worker/"

# Every endpoint a machine principal may reach (BE-0414 unit 3), as `(method, path)` pairs — the
# pipeline's whole sequence: probe for a build, publish the three artifact kinds, dispatch a run,
# then watch it. `GET /api/runs` and the job poll below are how it learns the outcome; both are
# scoped to the machine's own org at the operation, since the gate admits a path and never a tenant.
_MACHINE_PATHS = frozenset(
    {
        ("POST", "/api/artifacts/config"),
        ("POST", "/api/artifacts/scenarios"),
        ("POST", "/api/artifacts/binary"),
        ("GET", "/api/artifacts/exists"),
        ("POST", "/api/run"),
        ("GET", "/api/runs"),
    }
)


def _is_machine_job_route(path: str) -> bool:
    """Whether *path* is the job poll a machine may read (BE-0414 unit 3).

    Matched by shape because the job id is in the path, the same way `_is_worker_route` matches the
    upload-URL route. `/api/jobs/{id}` only — not its `/events` stream, which each backend serves
    off the uniform route table with its own streaming plumbing; a pipeline learns the same outcome
    by polling, so opening a second, differently-shaped path buys nothing.

    Admitting the path is not admitting the job: `job_view` refuses one belonging to another org.
    """
    return (
        path.startswith("/api/jobs/")
        and "/" not in path[len("/api/jobs/") :]
        and len(path) > len("/api/jobs/")
    )


def _is_worker_route(path: str) -> bool:
    """Whether *path* is a control-plane route a worker authenticates to with the shared token
    (BE-0313). Once OAuth is configured the token authorizes only these — never a human session or
    another endpoint. Beyond the `/api/worker/*` routes (lease / heartbeat / result / artifact-urls /
    scenario-url), a worker also requests evidence upload URLs at `POST /api/runs/{id}/upload-urls`
    (BE-0257), which sits outside that prefix — so it is matched by shape here, the same way
    `required_role` already treats it as worker traffic with no role gate."""
    return path.startswith(_WORKER_PREFIX) or (
        path.startswith("/api/runs/") and path.endswith("/upload-urls")
    )


def _is_frontend_module(path: str) -> bool:
    """Whether *path* is a serve.*.mjs frontend module route (BE-0247). Matched by shape, not an
    exact name list, so `gate` stays independent of the handler that owns that list — this only
    grants the auth exemption; the serving side still validates the exact name (traversal safety)."""
    return path.startswith("/serve.") and path.endswith(".mjs")


def allowed_hosts(host: str) -> frozenset[str]:
    """The `Host`-header hostnames a server bound to *host* accepts (BE-0121).

    A loopback bind accepts every loopback name; a specific host adds that name (still reachable via
    loopback locally). A wildcard bind (``0.0.0.0`` / ``::`` / empty) can't enumerate its reachable
    names — the operator chose broad exposure, often behind a proxy with its own hostname — so it
    returns an empty set, which disables Host enforcement (CSRF stays the cross-origin guard).
    """
    if host in ("", "0.0.0.0", "::"):  # noqa: S104 — matching a wildcard bind, not binding one
        return frozenset()
    normalized = host.lower()
    if normalized in _LOOPBACK_HOSTS:
        return _LOOPBACK_HOSTS
    return _LOOPBACK_HOSTS | {normalized}


def host_allowed(allowed: frozenset[str], host_header: str) -> bool:
    """Whether *host_header* names an interface serve is bound to (BE-0121 DNS-rebinding defense).

    An empty allowlist (a wildcard bind, whose reachable names can't be enumerated) accepts any
    Host; a loopback/named bind enforces its own names, so a page that rebinds a hostname to
    127.0.0.1 can't reach the loopback server through a same-origin request.
    """
    if not allowed:
        return True
    return urlparse(f"//{host_header}").hostname in allowed


def csrf_ok(origin: str | None, host_header: str) -> bool:
    """CSRF defense (BE-0051), defense-in-depth atop the SameSite session cookie: if an `Origin`
    header is present it must match the `Host`. Non-browser clients (no Origin, no ambient cookie)
    are allowed; a cross-origin browser request is blocked.
    """
    if not origin:
        return True
    return urlparse(origin).netloc == host_header


def is_open(method: str, path: str, *, oidc: bool = False) -> bool:
    """Whether this request may skip authentication even when a token is configured — the login UI,
    the OAuth round-trip, the login endpoint itself, and the frontend ES-module routes (BE-0247), so
    the login UI's JS loads before auth exactly as the old inlined index script did.

    The OIDC exchange joins them when *oidc* says this deployment configured one (BE-0414): it is
    the machine's way *in*, so it can require no prior serve credential — only a verifiable OIDC
    token, which it checks itself. A deployment with no expected audience leaves it closed, the
    same fail-closed rule that disables the caller shape everywhere else.
    """
    return (
        (method == "GET" and (path in _OPEN_GET_PATHS or _is_frontend_module(path)))
        or (method == "POST" and path == _LOGIN_PATH)
        or (oidc and method == "POST" and path == OIDC_EXCHANGE_PATH)
    )


def is_authorized(
    auth: SessionManager, authorization: str, session_value: str | None, *, path: str
) -> bool:
    """A request is authorized by a valid `Authorization: Bearer <token>` header or a valid session
    cookie (the browser, after signing in).

    Once GitHub OAuth is configured, the shared token narrows to worker traffic (BE-0313): a valid
    Bearer token authorizes only the `/api/worker/*` routes, closing the operator-credential reach
    over every other endpoint that carried no identity to check a role for. Without OAuth (the
    single-Mac token deployment) the Bearer token still authorizes any endpoint, unchanged.
    """
    # A valid Bearer token authorizes when OAuth is off (it reaches everything) or, once OAuth is on,
    # only on a worker route. Otherwise fall through to the session check — a Bearer request usually
    # carries no session, so a non-worker request with OAuth configured is denied here.
    if (
        authorization.startswith("Bearer ")
        and auth.check_token(authorization[len("Bearer ") :])
        and (auth.oauth is None or _is_worker_route(path))
    ):
        return True
    return session_value is not None and auth.valid_session(session_value)


def actor_for(auth: SessionManager, session_value: str | None) -> str | None:
    """The GitHub login bound to this request's session, if any — used to attribute audit entries
    (BE-0015 7c). None for a token/Bearer request or no session."""
    return auth.sessions.identity(session_value) if session_value else None


def principal_for(auth: SessionManager, session_value: str | None) -> Principal | None:
    """Who this request's session belongs to, in **one** read (BE-0414).

    A machine session authenticates exactly like a human one — `is_authorized` already accepts any
    valid session cookie — so the gate has to ask which it is holding before deciding *which* gate
    governs it. Reading the kind the store recorded, rather than the shape of the identity string,
    is what keeps a human session from ever reaching the machine branch.

    The whole principal comes back rather than only a machine one, so a backend derives both the
    kind and the identity from a single snapshot. Asking twice opened a window: between a
    `machine_principal` read and a separate `actor_for` read, a session whose short time-to-live
    crossed — or which a concurrent org retirement revoked — answers None to both, and a caller
    that is neither machine nor human falls through to the identity-less shared-token shape, which
    is full access. One read cannot disagree with itself that way.
    """
    return auth.sessions.principal(session_value) if session_value else None


def forbidden_for_machine(method: str, path: str, *, org: str | None) -> bool:
    """Whether a machine principal acting as *org* is refused this request (BE-0414 unit 3).

    An allowlist, not a rank. A pipeline is not a person, so no viewer / editor / admin describes
    it: four of the endpoints below are admin today, and granting a machine that *rank* would carry
    config rebinding and the operator-secret reads along with it. Naming endpoints keeps its reach
    to publishing an artifact and starting a run — the whole of what a pipeline needs — while
    `required_role` goes on deciding what a human needs. Neither gate widens the other.

    Default deny: anything not named here is refused, so a route added later is closed to a machine
    until someone opens it deliberately. That is why this is an allowlist rather than a list of
    refusals — `POST /api/compose` and `GET /api/config/content` are refused by saying nothing about
    them, not by being remembered.

    Refused outright when *org* is None. The exchange is the only thing that ever sets one, so a
    machine principal without it is a session row this version does not understand
    (`sessions.principal.kind_from_stored`) — and every allowlisted operation scopes itself by that
    org, so admitting one with none would fall back to the `default` tenant.

    Unconditional of the database on purpose: `forbidden_for_role` returns *allowed* when no
    repository is wired ("DB-less = full access"), and an allowlist conditioned the same way would
    inherit that fail-open. No machine principal can exist without a database — the exchange
    refuses one — so this is belt-and-braces rather than a live path.
    """
    if org is None:
        return True
    return (method, path) not in _MACHINE_PATHS and not (
        method == "GET" and _is_machine_job_route(path)
    )
