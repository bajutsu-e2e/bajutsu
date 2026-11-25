"""Organization-scoped API keys (BE-0509): their format, their hashing, and the bearer check.

A key is written `bajutsu_<id>_<secret>`. The identifier is random but not secret, so it finds the
row; the secret is stored only as a SHA-256 hash. A fast hash is enough because the secret is
random, not a password a person chose.

`admit` is the one entry point both serve backends call, so they cannot disagree on who a key is
(BE-0253). It must import without FastAPI or SQLAlchemy, like `gate`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from bajutsu.serve import oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.gate import forbidden_for_api_key
from bajutsu.serve.sessions import API_KEY, Principal, api_key_identity
from bajutsu.serve.state import ServeState

_logger = logging.getLogger(__name__)

_PREFIX = "bajutsu"
_ID_BYTES = 8
_SECRET_BYTES = 32
_ID_RE = re.compile(rf"[0-9a-f]{{{_ID_BYTES * 2}}}")

# A key is never `admin`, so it can never issue or revoke keys.
KEY_ROLES = ("viewer", "editor")
DEFAULT_EXPIRY_DAYS = 90
MAX_EXPIRY_DAYS = 365

# How stale the recorded last-used time may be: one write a minute for each key, however busy.
_TOUCH_INTERVAL = timedelta(minutes=1)


@dataclass(frozen=True)
class NewKey:
    """A freshly generated key. `value` is the only copy of the secret there will ever be."""

    id: str
    secret_hash: str
    value: str


@dataclass(frozen=True)
class Admission:
    """The verdict on a bearer value that names a key.

    Attributes:
        status: None to admit the request, else the HTTP status to refuse it with.
        principal: Who the request acts as; None when the key itself was refused (401).
    """

    status: int | None
    principal: Principal | None = None


def new_key() -> NewKey:
    """Generate an identifier and a secret."""
    key_id = secrets.token_hex(_ID_BYTES)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return NewKey(id=key_id, secret_hash=hash_secret(secret), value=f"{_PREFIX}_{key_id}_{secret}")


def hash_secret(secret: str) -> str:
    """The stored form of a key's secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_key(value: str) -> tuple[str, str] | None:
    """`(identifier, secret)` when *value* has the key shape, else None.

    The identifier holds no underscore, so the first two splits are unambiguous even though the
    secret may hold more.
    """
    parts = value.split("_", 2)
    if len(parts) != 3 or parts[0] != _PREFIX or not _ID_RE.fullmatch(parts[1]) or not parts[2]:
        return None
    return parts[1], parts[2]


def admit(state: ServeState, authorization: str, method: str, path: str) -> Admission | None:
    """Judge the request's `Authorization` header as an API key.

    None means it is not one — no bearer, not the key shape, an identifier no row has, or no
    database — and the caller carries on to the shared-token and session checks, so an operator's
    token that happens to look like a key keeps working.

    A key that is recognized is judged here and nowhere else: 401 with no hint when its secret is
    wrong, it expired, it was revoked, or its org is gone; 403 off the allowlist or above its role.
    Every use of a valid key, refused or not, is audited.
    """
    repository = state.repository
    if repository is None or not authorization.startswith("Bearer "):
        return None
    parsed = parse_key(authorization[len("Bearer ") :])
    if parsed is None:
        return None
    key_id, secret = parsed
    key = repository.get_api_key(key_id)
    if key is None:
        return None
    now = datetime.now(UTC)
    if (
        not hmac.compare_digest(hash_secret(secret), key.secret_hash)
        or key.revoked_at is not None
        or key.expires_at <= now
        # A retired org is a soft delete, so no cascade ever reaches its keys.
        or repository.get_org(key.org_id) is None
    ):
        return Admission(status=401)
    principal = Principal(
        identity=api_key_identity(key.id), org=key.org_id, kind=API_KEY, role=key.role
    )
    refused = forbidden_for_api_key(method, path, role=key.role)
    oplog.bind_actor(api_key_identity(key.id))
    repository.touch_api_key(key.id, at=now, min_interval=_TOUCH_INTERVAL)
    _record_audit(
        state,
        principal.identity,
        key.org_id,
        "apikey.use",
        path,
        {"method": method, "allowed": not refused},
    )
    oplog.log_event(
        _logger,
        "apikey.use",
        f"api key {key.id} used for {method} {path}",
        org=key.org_id,
        allowed=not refused,
    )
    return Admission(status=403 if refused else None, principal=principal)
