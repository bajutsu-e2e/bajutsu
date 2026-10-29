"""Capability tokens that route a job to a worker able to run it (BE-0166).

A heterogeneous Mac pool can't run every job on every worker: a job needing iOS 18 must not land
on an iOS-17-only worker, and a `web` job must never land on an idb (Mac) worker. Routing is
expressed as sets of string capability *tokens* — a job carries the set it **requires**, a worker
advertises the set it can **serve**, and a worker may lease a job only when the job's required set
is a subset of the worker's advertised set (an ``anyof:`` group is met by any one member). This is
purely about *which* idle worker picks a job up; it never touches the deterministic `run` verdict
(prime directive 1).

The module is intentionally pure — no SQLAlchemy, no network — so it is imported freely on the
dispatch (control-plane) and worker paths alike, and unit-tested with no Simulator. The two seams
that read a real inventory, `simctl_capabilities` for Simulators and `devicectl_capabilities` for
real devices, take an injected reader, so the gate drives them with captured JSON.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import get_args

from bajutsu.common.backend_cli import simctl as _simctl
from bajutsu.common.capability.route_tokens import (
    UNCHECKED_PROVENANCE_KEY,
    UNCHECKED_PROVENANCE_VALUE,
    advertised_route_tokens,
    is_route_token,
    token_route,
)
from bajutsu.common.capability.settings_support import Route
from bajutsu.common.capability.worker_capability import (
    DEVICE_ROUTING_PREFIX,
    ENVIRONMENT_ROUTING_PREFIX,
    HOST_ROUTING_PREFIX,
    MACHINE_ROUTING_PREFIX,
    reserved_routing_tokens,
)
from bajutsu.common.config.schema.platform.ios import IosFamily
from bajutsu.common.devices.facts import DeviceReadError, ios_device_families

# The backend axis (idb vs web vs android): `platform:ios` is served by a Mac idb worker,
# `platform:web` by the Linux Playwright worker container (BE-0173). Kept a distinct prefix so a
# free operator-declared token (`ios18`, `ipad`) can never collide with the platform axis.
PLATFORM_PREFIX = "platform:"

# The environment axis (BE-0448): a worker whose `worker.yaml` names a device cloud as its
# `environment` advertises `environment:<name>`, and a cloud-batch job requires exactly that token.
# Reserved: only a worker that registered the named provider may advertise it, so an operator's
# free-form tokens can never pull a cloud job onto a worker that holds no provider.
ENVIRONMENT_PREFIX = ENVIRONMENT_ROUTING_PREFIX

# The host axis (BE-0450): every worker advertises `host:<os>` for the operating system it runs on,
# and a job whose driver runs on one host operating system only (xcuitest: macOS) requires it, so the
# job never reaches a worker whose machine cannot start that driver.
HOST_PREFIX = HOST_ROUTING_PREFIX

# The machine axis (BE-0449): a job that names its config, scenario, and runs directory by their
# paths on the server's machine — a local start's run, triage, record, and crawl — requires
# `machine:<id>`, the server's own identity, and only a worker on that machine advertises it. A job
# that ships its files (materials, a bundle, overrides) carries no machine token and routes anywhere.
MACHINE_PREFIX = MACHINE_ROUTING_PREFIX

# The real-device axis (BE-0468): a worker advertises `device:iphone` / `device:ipad` for each family
# among the real devices connected to it, and a real-device run whose target names a `family`
# requires it. Reserved, so a hand-written token cannot claim a device the worker lacks.
DEVICE_PREFIX = DEVICE_ROUTING_PREFIX

# An any-of group (BE-0468): one stored token that a worker satisfies by advertising any one of its
# members, written `anyof:<token>|<token>`. A `family` priority list needs it, since any one family
# suffices and a subset test cannot say so. It lives in the job's one capability list rather than a
# column of its own, so the lease, the unroutable count, and the no-worker notice — which all judge
# that list with `can_serve` — agree on it with no schema change.
ANY_OF_PREFIX = "anyof:"
_ANY_OF_SEPARATOR = "|"

# What a machine identity may look like: one token, so it never splits in a comma/space list and
# never carries a prefix of its own.
_MACHINE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

# Operator override: extra capability tokens a worker advertises beyond its platform + Simulator
# inventory (comma/space separated), e.g. `ios18,ipad`. Deprecated (BE-0450): still honored, with a
# notice, for one release.
WORKER_CAPABILITIES_ENV = "BAJUTSU_WORKER_CAPABILITIES"

# The server machine identity a worker advertises as `machine:<id>`, when `--machine-id` is unset.
MACHINE_ID_ENV = "BAJUTSU_WORKER_MACHINE_ID"

_SPLIT = re.compile(r"[,\s]+")


def platform_capability(platform: str) -> str:
    """The backend-axis token for a platform, e.g. ``platform:ios``."""
    return f"{PLATFORM_PREFIX}{platform}"


def environment_capability(environment: str) -> str:
    """The environment-axis token for a device cloud, e.g. ``environment:devicefarm``."""
    return f"{ENVIRONMENT_PREFIX}{environment}"


def host_capability(host: str) -> str:
    """The host-axis token for an operating system, e.g. ``host:darwin``."""
    return f"{HOST_PREFIX}{host}"


def machine_capability(machine_id: str) -> str:
    """The machine-axis token for a server machine identity, e.g. ``machine:3f2a…``.

    Raises:
        ValueError: *machine_id* is empty or is not one token of letters, digits, ``.``, ``_``, ``-``.
    """
    if not _MACHINE_ID.fullmatch(machine_id):
        raise ValueError(
            f"invalid machine id {machine_id!r}: expected 1-128 letters, digits, '.', '_' or '-'"
        )
    return f"{MACHINE_PREFIX}{machine_id}"


def device_capability(family: str) -> str:
    """The real-device-axis token for a device family, e.g. ``device:ipad``."""
    return f"{DEVICE_PREFIX}{family}"


def any_of_capability(tokens: Iterable[str]) -> str:
    """The token a worker satisfies by advertising any one of *tokens*; a lone token stands as itself.

    The members are sorted, so two jobs that accept the same tokens store the same group.

    Raises:
        ValueError: *tokens* is empty, or a member carries the ``|`` separator or is itself a group,
            either of which `can_serve` would split into members the job never named.
    """
    members = sorted(set(tokens))
    if not members:
        raise ValueError("an any-of group needs at least one token")
    if bad := [m for m in members if _ANY_OF_SEPARATOR in m or m.startswith(ANY_OF_PREFIX)]:
        raise ValueError(f"an any-of member may not contain '|' or nest a group: {', '.join(bad)}")
    if len(members) == 1:
        return members[0]
    return ANY_OF_PREFIX + _ANY_OF_SEPARATOR.join(members)


def device_requirements(kinds: Sequence[str], family: str | Sequence[str] | None) -> list[str]:
    """The tokens a target's iOS runs require of a worker for their `family` (BE-0468).

    A serve job runs every listed kind of its target, so it requires one entry per kind: a real-device
    run the ``device:`` token of its family, and a Simulator run the inventory's ``iphone`` /
    ``ipad`` token. A family priority list becomes an any-of group, because any one family suffices;
    which family the run takes is still decided on the leasing worker, over its own pool.

    Args:
        kinds: The iOS kinds the job runs on, in order.
        family: The target's `runsOn.ios.family`: one family, a priority list, or None.

    Returns:
        The sorted tokens; empty when the target names no family.
    """
    families = [family] if isinstance(family, str) else list(family or [])
    if not families:
        return []
    return sorted(
        {
            any_of_capability(device_capability(f) if kind == "device" else f for f in families)
            for kind in kinds
        }
    )


def routed_capabilities(
    required: Iterable[str], *, names_host_paths: bool, machine_id: str | None
) -> list[str]:
    """The capability set a queued job is stored with: *required* plus, for a job that names files
    by their paths on the server's machine, that machine's ``machine:<id>`` token.

    The lease, the unroutable count, and the no-worker notice all judge the stored set with
    `can_serve`, so stamping it here is what keeps the three agreeing that only a worker on the
    server's machine can take the job (BE-0449).

    Args:
        required: The job's own requirement (`required_capabilities`, or an environment token).
        names_host_paths: Whether the job ships none of its files and names them by server paths.
        machine_id: The server's machine identity; None stamps nothing (a server with no identity).
    """
    tokens = set(required)
    if names_host_paths and machine_id:
        tokens.add(machine_capability(machine_id))
    return sorted(tokens)


def required_host(
    actuators: Iterable[str],
    host_os_for: Callable[[str], frozenset[str] | None] | None = None,
) -> str | None:
    """The one host operating system every actuator in *actuators* is bound to, or None.

    A fallback list such as ``[xcuitest, playwright]`` requires no host, since `playwright` runs
    anywhere and the worker may resolve to it; an actuator with no driver yet adds no constraint
    either, as the worker cannot select it.

    Args:
        actuators: The actuators a job's target requests, in order.
        host_os_for: The driver host-requirement lookup; `backends.host_os_for` when None.
    """
    if host_os_for is None:
        from bajutsu.common.backends import host_os_for as default_host_os_for

        host_os_for = default_host_os_for
    hosts: set[str] = set()
    for actuator in actuators:
        try:
            bound = host_os_for(actuator)
        except ValueError:
            return None
        if bound is None or len(bound) != 1:
            return None
        hosts |= bound
    return next(iter(hosts)) if len(hosts) == 1 else None


def reserved_tokens(tokens: Iterable[str]) -> list[str]:
    """The tokens among *tokens* that carry a reserved routing prefix (``environment:``, ``host:``,
    ``machine:``, ``device:``, ``cap:``, ``setting:``), sorted."""
    return reserved_routing_tokens(tokens)


def parse_capabilities(raw: str | None) -> set[str]:
    """Split an operator-supplied token list (comma or whitespace separated) into a token set.

    Empty / ``None`` yields the empty set, so an unset override adds no requirements.
    """
    if not raw:
        return set()
    return {token for token in _SPLIT.split(raw.strip()) if token}


def required_capabilities(platform: str, *, host: str | None = None) -> list[str]:
    """The capability set a job needs: its platform axis and its host.

    Returned sorted and de-duplicated so the value stored on the job row is canonical — two jobs
    with the same requirements always serialize identically.

    Args:
        platform: The target's platform axis; empty adds no ``platform:*`` token.
        host: The host operating system the target's driver is bound to (`required_host`); None
            adds no ``host:*`` token.
    """
    tokens = {platform_capability(platform)} if platform else set()
    if host:
        tokens.add(host_capability(host))
    return sorted(tokens)


def can_serve(required: Iterable[str], advertised: Iterable[str]) -> bool:
    """Whether a worker advertising *advertised* may run a job requiring *required*.

    A subset test: the worker must advertise *every* token the job requires. A job with no declared
    requirement (the empty set) is servable by any worker, so an un-annotated job routes as before.
    One rule narrows it (BE-0448): a worker advertising an ``environment:*`` token serves only jobs
    that require that token, since the empty set would otherwise hand it every local job. A job
    tied to the server's machine needs no rule of its own: it requires ``machine:<id>``, which only a
    worker on that machine advertises (`routed_capabilities`). An ``anyof:`` group in *required* is
    met by any one of its members (`any_of_capability`, BE-0468).
    """
    needed, offered = set(required), set(advertised)
    environments = {t for t in offered if t.startswith(ENVIRONMENT_PREFIX)}
    if environments and not environments & needed:
        return False
    if not checks_routes(offered):
        # A worker from before BE-0467 advertises no `cap:` / `setting:` token, so it is matched on
        # the other tokens alone: an upgrade never strands a queue. The lease records on the job that
        # such a worker ran it (`UNCHECKED_PROVENANCE`).
        needed = {t for t in needed if not is_route_token(t)}
    groups = {t for t in needed if t.startswith(ANY_OF_PREFIX)}
    if any(
        not offered & set(group.removeprefix(ANY_OF_PREFIX).split(_ANY_OF_SEPARATOR))
        for group in groups
    ):
        return False
    return needed - groups <= offered


# What a lease stamps on the job's provenance when a worker from before BE-0467 takes a job that needs
# route tokens: the run's report then shows that its settings went unchecked (`report_notices`).
UNCHECKED_PROVENANCE: dict[str, str] = {UNCHECKED_PROVENANCE_KEY: UNCHECKED_PROVENANCE_VALUE}


def unchecked_lease(required: Iterable[str], advertised: Iterable[str]) -> bool:
    """Whether a worker advertising *advertised* serves *required* without its route tokens."""
    return any(is_route_token(t) for t in required) and not checks_routes(advertised)


def claim_refusal(required: Iterable[str], current: Iterable[str] | None) -> str | None:
    """Why a worker whose routes now advertise *current* must not run a job requiring *required*.

    The lease matched the job against what the worker advertised at registration; this re-checks
    the route tokens against what its environment offers at claim, before it leases a device
    (BE-0467). None when the worker computes no route tokens (*current* None, a cloud batch) or
    every route token the job needs is still there.
    """
    if current is None:
        return None
    offered = set(current)
    missing = sorted(t for t in required if is_route_token(t) and t not in offered)
    if not missing:
        return None
    return (
        "this worker's environment no longer offers what the job was routed by: missing "
        + ", ".join(missing)
    )


def checks_routes(advertised: Iterable[str]) -> bool:
    """Whether a worker advertising *advertised* routes by `cap:` / `setting:` tokens (BE-0467)."""
    return any(is_route_token(t) for t in advertised)


def route_refusal(required: Iterable[str], live: Iterable[Iterable[str]]) -> list[str] | None:
    """The route tokens no live worker driving the job's routes advertises, or None to queue it.

    A job whose routes no live worker drives stays queued, so a submission during a cold start or a
    worker restart is never refused. Refusal needs a live worker that does drive one of those routes
    and lacks a route token the job needs, with no live worker able to serve the job at all — a
    worker from before BE-0467 still serves it on today's tokens.

    Returns:
        The missing tokens of the closest such worker, sorted; None when the job should queue.
    """
    needed = set(required)
    routes = {token_route(t) for t in needed if is_route_token(t)}
    workers = [set(adv) for adv in live]
    if not routes or any(can_serve(needed, adv) for adv in workers):
        return None
    drivers = [adv for adv in workers if any(token_route(t) in routes for t in adv)]
    gaps: list[set[str]] = [{t for t in needed if is_route_token(t)} - adv for adv in drivers]
    if not gaps or not all(gaps):
        return None
    closest = gaps[0]
    for gap in gaps[1:]:
        if len(gap) < len(closest):
            closest = gap
    return sorted(closest)


def worker_capabilities(
    platforms: Iterable[str],
    *,
    override: str | None = None,
    run: _simctl.RunFn | None = None,
    routes: Iterable[Route] | None = None,
) -> set[str]:
    """Assemble the capability set a worker advertises.

    Args:
        platforms: The backend axes this worker can drive (``ios`` for a Mac idb worker, ``web`` for
            the Playwright container) — each becomes a ``platform:*`` token.
        override: The deprecated operator pin from `WORKER_CAPABILITIES_ENV` / ``--capabilities``;
            its tokens are added verbatim, so an operator can force ``ios18``/``ipad`` regardless of
            inventory.
        run: A `simctl` runner. When given, the installed Simulator inventory contributes ``iosNN``
            runtime and ``iphone``/``ipad`` device-class tokens (`simctl_capabilities`); omit it on a
            worker with no Simulators (e.g. the web container).
        routes: The environment routes the worker drives (`worker_routes`); each adds its
            ``cap:`` and ``setting:`` tokens (BE-0467). None computes none, as for a worker that
            predates them; an empty list still marks the worker route-aware.
    """
    caps = {platform_capability(p) for p in platforms if p}
    if routes is not None:
        caps |= advertised_route_tokens(routes)
    caps |= parse_capabilities(override)
    if run is not None:
        caps |= simctl_capabilities(run)
    return caps


def devicectl_capabilities(
    read: Callable[[], set[str]] = ios_device_families,
    on_error: Callable[[str], None] | None = None,
) -> set[str]:
    """The ``device:`` tokens of the real devices connected to this worker (best-effort, BE-0468).

    Each family a `family` condition can name contributes ``device:<family>``; another family (an
    Apple TV) adds nothing. A host whose devicectl cannot be read advertises no real device rather
    than stopping the worker, as `simctl_capabilities` does for the Simulator inventory: a job that
    needs a device then waits for a worker that has one.

    Args:
        read: The real-device family reader; `ios_device_families` over devicectl when omitted.
        on_error: Receives the read's failure message, so the worker can say why it advertises
            no real device instead of dropping them without a trace.
    """
    try:
        families = read()
    except DeviceReadError as exc:
        if on_error is not None:
            on_error(str(exc))
        return set()
    known = set(get_args(IosFamily))
    return {device_capability(f.lower()) for f in families if f.lower() in known}


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
