"""Capability tokens that route a job to a worker able to run it (BE-0166).

A heterogeneous Mac pool can't run every job on every worker: a job needing iOS 18 must not land
on an iOS-17-only worker, and a `web` job must never land on an idb (Mac) worker. Routing is
expressed as sets of string capability *tokens* — a job carries the set it **requires**, a worker
advertises the set it can **serve**, and a worker may lease a job only when the job's required set
is a subset of the worker's advertised set. This is purely about *which* idle worker picks a job
up; it never touches the deterministic `run` verdict (prime directive 1).

The module is intentionally pure — no SQLAlchemy, no network — so it is imported freely on the
dispatch (control-plane) and worker paths alike, and unit-tested with no Simulator. The one seam
that reads a real Simulator inventory, `simctl_capabilities`, takes an injected `RunFn`, so the
gate drives it with captured `simctl` JSON.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from bajutsu import simctl as _simctl

# The backend axis (idb vs web vs android): `platform:ios` is served by a Mac idb worker,
# `platform:web` by the Linux Playwright worker container (BE-0173). Kept a distinct prefix so a
# free operator-declared token (`ios18`, `ipad`) can never collide with the platform axis.
PLATFORM_PREFIX = "platform:"

# Operator override: extra capability tokens a worker advertises beyond its platform + Simulator
# inventory (comma/space separated), e.g. `ios18,ipad`. The pinning escape hatch the design keeps.
WORKER_CAPABILITIES_ENV = "BAJUTSU_WORKER_CAPABILITIES"

_SPLIT = re.compile(r"[,\s]+")


def platform_capability(platform: str) -> str:
    """The backend-axis token for a platform, e.g. ``platform:ios``."""
    return f"{PLATFORM_PREFIX}{platform}"


def parse_capabilities(raw: str | None) -> set[str]:
    """Split an operator-supplied token list (comma or whitespace separated) into a token set.

    Empty / ``None`` yields the empty set, so an unset override adds no requirements.
    """
    if not raw:
        return set()
    return {token for token in _SPLIT.split(raw.strip()) if token}


def required_capabilities(platform: str, requires: Iterable[str] = ()) -> list[str]:
    """The capability set a job needs: its platform axis plus any operator-declared tokens.

    Returned sorted and de-duplicated so the value stored on the job row is canonical — two jobs
    with the same requirements always serialize identically.
    """
    tokens = {platform_capability(platform)} if platform else set()
    tokens.update(t for t in requires if t)
    return sorted(tokens)


def can_serve(required: Iterable[str], advertised: Iterable[str]) -> bool:
    """Whether a worker advertising *advertised* may run a job requiring *required*.

    A subset test: the worker must advertise *every* token the job requires. A job with no declared
    requirement (the empty set) is servable by any worker, so an un-annotated job routes as before.
    """
    return set(required) <= set(advertised)


def worker_capabilities(
    platforms: Iterable[str],
    *,
    override: str | None = None,
    run: _simctl.RunFn | None = None,
) -> set[str]:
    """Assemble the capability set a worker advertises.

    Args:
        platforms: The backend axes this worker can drive (``ios`` for a Mac idb worker, ``web`` for
            the Playwright container) — each becomes a ``platform:*`` token.
        override: The operator pin from `WORKER_CAPABILITIES_ENV` / ``--capabilities``; its tokens
            are added verbatim, so an operator can force ``ios18``/``ipad`` regardless of inventory.
        run: A `simctl` runner. When given, the installed Simulator inventory contributes ``iosNN``
            runtime and ``iphone``/``ipad`` device-class tokens (`simctl_capabilities`); omit it on a
            worker with no Simulators (e.g. the web container).
    """
    caps = {platform_capability(p) for p in platforms if p}
    caps |= parse_capabilities(override)
    if run is not None:
        caps |= simctl_capabilities(run)
    return caps


def simctl_capabilities(run: _simctl.RunFn) -> set[str]:
    """Capability tokens derived from the worker's Simulator inventory (best-effort, ``set()`` on any
    `simctl` failure).

    Each installed runtime contributes an ``iosNN`` token (major version only, so a job pinned to
    ``ios18`` matches any 18.x runtime), and each available device model contributes its device
    class (``iphone`` / ``ipad``). The heavy lifting — running `simctl` and tolerating its failures —
    is `simctl.device_catalog`; this only maps its ``{name, runtime}`` values to tokens.
    """
    caps: set[str] = set()
    for entry in _simctl.device_catalog(run).values():
        if token := _runtime_token(entry.get("runtime", "")):
            caps.add(token)
        if token := _device_class_token(entry.get("name", "")):
            caps.add(token)
    return caps


def _runtime_token(runtime_label: str) -> str | None:
    """``iosNN`` for an iOS runtime label like ``iOS 18.1`` (major version only), else ``None``."""
    match = re.match(r"\s*iOS\s+(\d+)", runtime_label)
    return f"ios{match.group(1)}" if match else None


def _device_class_token(device_name: str) -> str | None:
    """``iphone`` / ``ipad`` from a device model name, else ``None`` (an unknown/absent class adds
    no token rather than guessing)."""
    lowered = device_name.lower()
    if "ipad" in lowered:
        return "ipad"
    if "iphone" in lowered:
        return "iphone"
    return None
