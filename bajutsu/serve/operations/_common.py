"""Cross-cutting private helpers shared by several serve-operation submodules (BE-0127)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bajutsu.common.config import Effective, resolve
from bajutsu.common.config.schema import Config
from bajutsu.common.drivers import base as driver_base
from bajutsu.serve.authz import _target_forbidden
from bajutsu.serve.helpers import (
    valid_backend,
    valid_udid,
)
from bajutsu.serve.state import ServeState

# A live capture/enrich driver paired with the teardown that releases whatever backs it. Some
# backends leave nothing to release once the driver is dropped (idb was such a backend); XCUITest
# owns an `xcodebuild` runner subprocess that the session must tear down explicitly (BE-0290), so
# the factory returns the teardown alongside the driver rather than relying on drop/`close()`.
DriverSession = tuple[driver_base.Driver, Callable[[], None]]


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
    state: ServeState, target: str, actor: str | None, session: str | None
) -> tuple[str, tuple[Any, int] | None]:
    """The org resolution + cross-org guard shared by every start_* endpoint: resolve the actor's
    org and deny a target that belongs to another org (BE-0015; single-tenant never forbids).
    Returns ``(org, None)`` when allowed, or ``(org, (error, 403))`` for the caller to return.

    *session* is required rather than defaulted (BE-0393 unit 2): ownership is a property of the
    binding the request is running against, so a guard that silently fell back to the deployment's
    would answer from a partition the rest of the request is not using — and a defaulted parameter
    is how the first eight callers came to omit it.
    """
    org = state.org_of(actor)
    if _target_forbidden(state, org, target, session):
        return org, ({"error": "forbidden"}, 403)
    return org, None


def _session_effective(
    state: ServeState, config: Config, target: str, session: str | None = None, org: str = ""
) -> Effective:
    """Effective config for a live capture/enrich session, rebased against the requesting session's
    bound `cwd` like the CLI does for `run`/`record`; `confine=False` since bind already confined this
    config's paths."""
    return resolve(config, target).rebased(state.binding_for(session, org).cwd, confine=False)


def _close_quietly(driver: driver_base.Driver) -> None:
    """Release a driver that owns no separate resource: call its `close()` if it has one."""
    close = getattr(driver, "close", None)
    if callable(close):
        close()


def _default_driver_factory(eff: Effective, backends_list: list[str], udid: str) -> DriverSession:
    """Bring up a live driver for a capture/enrich session, cost-ordered like the run ladder;
    XCUITest needs a short-lived `xcodebuild` runner, so its teardown stops that runner explicitly
    instead of leaking the subprocess, while every other backend just gets an optional `close()`."""
    from bajutsu.common import backends

    actuator = backends.select_actuator_cost_first(backends_list or ["fake"])
    if actuator == "xcuitest":
        from bajutsu.platform_lifecycle.read_session import open_ios_read_driver

        driver, env = open_ios_read_driver(udid, eff)
        return driver, lambda: env.teardown(driver, eff)
    driver = backends.make_driver(actuator, udid)
    return driver, lambda: _close_quietly(driver)
