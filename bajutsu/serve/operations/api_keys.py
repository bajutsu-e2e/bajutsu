"""Issue, list, and revoke an organization's API keys (BE-0509): the `/api/orgs/{slug}/keys` trio.

Admin-only by their path (`authz.required_role`'s `/api/orgs/` branch) and audited. The audit
entries name the key identifier, never the secret.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from bajutsu.serve import api_keys, oplog
from bajutsu.serve.authz import _record_audit
from bajutsu.serve.server.db import ApiKeyRecord, Repository
from bajutsu.serve.state import ServeState

_logger = logging.getLogger(__name__)

_NAME_MAX = 100

# 409 rather than the org routes' 400: without a repository there is no per-organization identity
# for a key to carry, and the shared token already grants everything.
_NO_KEY_STORE = ({"error": "API keys need a database"}, 409)


def _org_repository(state: ServeState, slug: str) -> Repository | tuple[Any, int]:
    """The repository holding *slug*'s keys, or the refusal to answer instead."""
    if state.repository is None:
        return _NO_KEY_STORE
    if state.repository.get_org(slug) is None:
        return {"error": f"no org named {slug!r}"}, 404
    return state.repository


def create_api_key(
    state: ServeState, slug: str, body: dict[str, Any], *, actor: str | None = None
) -> tuple[Any, int]:
    """Issue a key for *slug* from ``{name, role, expiresInDays?}``.

    The reply is the only place the key ever appears. There is no `admin` role to ask for, so a
    key can never issue another.
    """
    repository = _org_repository(state, slug)
    if isinstance(repository, tuple):
        return repository
    raw_name = body.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name or len(name) > _NAME_MAX:
        return {"error": f"name is required, at most {_NAME_MAX} characters"}, 400
    role = body.get("role")
    if not isinstance(role, str) or role not in api_keys.KEY_ROLES:
        return {"error": f"role must be one of {', '.join(api_keys.KEY_ROLES)}"}, 400
    days = body.get("expiresInDays", api_keys.DEFAULT_EXPIRY_DAYS)
    # `bool` is an `int` subclass, and `true` days is a mistake rather than one day.
    if type(days) is not int or not 1 <= days <= api_keys.MAX_EXPIRY_DAYS:
        return {
            "error": f"expiresInDays must be an integer from 1 to {api_keys.MAX_EXPIRY_DAYS}"
        }, 400
    key = api_keys.new_key()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=days)
    repository.create_api_key(
        ApiKeyRecord(
            id=key.id,
            org_id=slug,
            name=name,
            role=role,
            secret_hash=key.secret_hash,
            created_by=actor,
            created_at=now,
            expires_at=expires_at,
        )
    )
    _audit(state, actor, "apikey.issue", slug, key.id, {"name": name, "role": role})
    return {
        "ok": True,
        "id": key.id,
        "key": key.value,
        "name": name,
        "role": role,
        "expiresAt": expires_at.isoformat(),
    }, 200


def list_api_keys(state: ServeState, slug: str) -> tuple[Any, int]:
    """*slug*'s unrevoked keys. No secret and no hash leaves the server."""
    repository = _org_repository(state, slug)
    if isinstance(repository, tuple):
        return repository
    return {
        "keys": [
            {
                "id": key.id,
                "name": key.name,
                "role": key.role,
                "createdBy": key.created_by,
                "expiresAt": key.expires_at.isoformat(),
                "lastUsedAt": key.last_used_at.isoformat() if key.last_used_at else None,
            }
            for key in repository.list_api_keys(slug)
        ]
    }, 200


def revoke_api_key(
    state: ServeState, slug: str, key_id: str, *, actor: str | None = None
) -> tuple[Any, int]:
    """Revoke one of *slug*'s keys.

    An identifier belonging to another org answers 404, never 403, so the route does not confirm
    that such a key exists.
    """
    repository = _org_repository(state, slug)
    if isinstance(repository, tuple):
        return repository
    if not repository.revoke_api_key(key_id, org_id=slug, at=datetime.now(UTC)):
        return {"error": "no such key"}, 404
    _audit(state, actor, "apikey.revoke", slug, key_id, {})
    return {"ok": True, "id": key_id}, 200


def _audit(
    state: ServeState,
    actor: str | None,
    action: str,
    slug: str,
    key_id: str,
    detail: dict[str, Any],
) -> None:
    """Record a key's issue or revocation in both the operations log and the audit trail."""
    oplog.log_event(_logger, action, f"{action} {key_id} for org {slug!r}", org=slug)
    detail = {**detail, "org": slug, "apiKey": key_id}
    if actor is None and state.repository is not None:
        # The shared token names nobody, which `_record_audit` skips. A key is a new credential, so
        # its issue and revocation are recorded anyway, under the org that holds it.
        state.repository.record_audit(
            org_id=slug, actor_id=None, action=action, target=key_id, detail=detail
        )
        return
    _record_audit(state, actor, state.org_of(actor), action, key_id, detail)
