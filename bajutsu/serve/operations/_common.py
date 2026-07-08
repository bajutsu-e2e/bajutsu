"""Cross-cutting private helpers shared by several serve-operation submodules (BE-0127)."""

from __future__ import annotations

from typing import Any

from bajutsu.drivers import base as driver_base
from bajutsu.serve.authz import _target_forbidden
from bajutsu.serve.helpers import (
    valid_backend,
    valid_udid,
)
from bajutsu.serve.state import ServeState


def _device_args(body: dict[str, Any]) -> tuple[str, str, tuple[Any, int] | None]:
    """Parse + validate the device selectors common to run/record/crawl: ``(backend, udid, error)``.
    *error* is a ``(payload, status)`` tuple when a value is invalid (a free-text backend/udid must
    not reach the spawned argv — BE-0051), else None so the caller proceeds."""
    backend = str(body.get("backend", "") or "")
    if backend and not valid_backend(backend):
        return backend, "", ({"error": f"unknown backend: {backend}"}, 400)
    udid = str(body.get("udid", "") or "")
    if udid and not valid_udid(udid):
        return backend, udid, ({"error": "invalid udid"}, 400)
    return backend, udid, None


def _resolve_org_or_forbid(
    state: ServeState, target: str, actor: str | None
) -> tuple[str, tuple[Any, int] | None]:
    """The org resolution + cross-org guard shared by every start_* endpoint: resolve the actor's
    org and deny a target that belongs to another org (BE-0015; single-tenant never forbids).
    Returns ``(org, None)`` when allowed, or ``(org, (error, 403))`` for the caller to return."""
    org = state.org_of(actor)
    if _target_forbidden(state, org, target):
        return org, ({"error": "forbidden"}, 403)
    return org, None


def _default_driver_factory(target: str, backend: str, udid: str) -> driver_base.Driver:
    from bajutsu import backends

    actuator = backends.select_actuator([backend] if backend else ["fake"])
    return backends.make_driver(actuator, udid)
