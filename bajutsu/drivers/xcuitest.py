"""XCUITest backend — semantic actuation over a loopback HTTP channel (BE-0019).

Unlike a coordinate-CLI backend that taps frame-centre coordinates, XCUITest actuates from a resident
XCTest runner living on the Simulator, so Python and that runner talk over a small `127.0.0.1`
channel — the same loopback pattern `network.py` already uses, in the Python→runner direction. This
module is the **Python side** of that channel: it builds the requests, parses the responses, and maps
failures onto the shared `Driver` exceptions. The runner itself (a generic XCTest target in
`BajutsuKit`) is a separate, on-device slice; here the transport is injectable so the request/response
logic is exercised against a fake — no Simulator on the gate.

The crux is **element addressing**: resolution stays Python-side (`resolve_unique`), so the driver
acts on exactly the element it resolved by sending that element's opaque *handle* the runner minted —
never a re-resolved predicate that could match a different element. The runner derives that handle
from the element's identity (identifier / label / traits), so a re-snapshot of an unchanged screen
re-issues the *identical* handle; a handle goes stale only when the element leaves the screen or
changes identity (BE-0312). A `stale` reply is still treated as a trigger to re-query rather than an
immediate failure (BE-0289): the actuation is re-issued only while the same selector still resolves
Python-side to a single element, and fails loudly the moment it resolves to none (`ElementNotFound`)
or many (`AmbiguousSelector`) — so the retry tolerates a transient `stale` without ever absorbing a
real disappearance.

Selection-wiring (adding `xcuitest` to `backends.IMPLEMENTED` / `make_driver`, plus the device
availability probe) lands with the runner; today the driver is constructed directly (e.g. in tests)
and `backends.capabilities_for` reads its `CAPABILITIES` without a device.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from bajutsu.device_os import DeviceOS
from bajutsu.drivers import base
from bajutsu.drivers.actuation import Actuation, ActuationLog, Drained


class XcuitestChannelError(RuntimeError):
    """The runner channel failed: it never came up, stopped answering, or returned a bad response.

    An infrastructure failure, kept distinct from a test outcome — a crashed/absent runner fails the
    run loudly rather than being read as "element not found".
    """


class XcuitestRunnerCrashError(XcuitestChannelError, base.BackendCrashError):
    """The runner died mid-run: the loopback channel stayed unreachable past the transient-retry budget (BE-0287).

    Also a `base.BackendCrashError`, so the backend-agnostic run pipeline recovers it uniformly:
    it discards the dead lease, cold-respawns a fresh runner, and re-runs the whole scenario (bounded).

    A crash outlives the BE-0207 retry (a sub-second blip smoother), so it is kept distinct from both a
    transient blip and a decoded test outcome: it names an honest "the runner crashed" failure, so a
    lost two-finger gesture never masquerades as an assertion mismatch (`actual='idle'`). `delivered`
    records whether the failed call had reached the runner, so the crash-recovery layer can tell a
    safe-to-re-issue read from a write that must not be re-applied. `hung` narrows `delivered` further
    (BE-0354): the request reached the runner and no response ever came, the one shape that identifies
    a wedged automation session rather than a runner that died.
    """

    def __init__(
        self, message: str, *, method: str = "", delivered: bool = False, hung: bool = False
    ) -> None:
        super().__init__(message)
        self.method = method
        self.delivered = delivered
        self.hung = hung


@dataclass(frozen=True)
class _Reply:
    """A decoded runner response.

    `elements` carries the `GET /elements` payload (each item is the normalized element fields plus
    its `handle`); `png` carries raw `GET /screenshot` bytes. `raw` is the undecoded JSON body — kept
    alongside the parsed fields (not just for `/elements`; cheap, it is the same bytes already read)
    so `_query_with_handles` can hand the tree query's body to `RawSourceProvider` without a second
    round trip.
    """

    status: str
    elements: list[dict[str, Any]] | None = None
    png: bytes | None = field(default=None, repr=False)
    size: base.Point | None = None  # the `GET /screen` viewport (w, h), BE-0326
    raw: bytes | None = field(default=None, repr=False)


# (method, path, json body) -> decoded reply. Injectable so the channel logic is tested without a
# runner; the default talks HTTP to the runner's loopback server.
TransportFn = Callable[[str, str, Mapping[str, Any] | None], _Reply]

# Statuses the runner returns for an actuation request. `ok` succeeds; `stale` / `not-found` are test
# outcomes (the element vanished / could not be actuated); any other status is a runner/infra error.
_OK = "ok"
_STALE = "stale"  # the resolved handle no longer maps to a live element (the screen changed)
_NOT_FOUND = "not-found"  # the runner could not act on the handle (no matching live element)
_NOT_HITTABLE = "not-hittable"  # the element is live but not reachable at its own point right now

# Socket timeout for a single runner *read* request (GET). BE-0105 replaced the per-attribute
# `/elements` walk (~10s+ per screen) with one `app.snapshot()`, so the 60s stopgap is reverted to a
# bounded window: generous enough for a cold first snapshot (XCUITest waits for the app to idle),
# tight enough that a wedged runner fails loudly rather than hanging. A transient read blip is
# absorbed by the BE-0207 retry, so this stays tight.
_SOCKET_TIMEOUT_SECONDS = 15

# Socket timeout for a single actuation *write* request (POST). A write synthesizes a real UI event —
# a two-finger gesture on a loaded CI host can take longer than a read — and BE-0207 must NOT re-issue
# a write after delivery (double-actuation risk), so a write cannot lean on the retry the way a read
# does. It gets ONE longer but still bounded window instead: enough headroom for a slow actuation on a
# contended host, while a genuinely wedged runner still fails loudly rather than hanging. Kept ≤ the
# job's per-step budget by a wide margin.
_ACTUATION_TIMEOUT_SECONDS = 30


def _timeout_for(method: str) -> float:
    """Per-attempt socket timeout for a channel call, chosen by its idempotency class.

    Reads (`GET`) get the tight `_SOCKET_TIMEOUT_SECONDS` and lean on the BE-0207 retry to absorb a
    transient blip; a write (`POST`) cannot be retried after delivery, so it gets the longer, still
    bounded `_ACTUATION_TIMEOUT_SECONDS` to tolerate a slow actuation on a loaded host.
    """
    return _SOCKET_TIMEOUT_SECONDS if method == "GET" else _ACTUATION_TIMEOUT_SECONDS


# Bounded retry for a *transient* transport hiccup (BE-0207), beside the per-attempt window above:
# `_SOCKET_TIMEOUT_SECONDS` still bounds each single attempt (a wedged runner fails fast per try),
# and these bound how many times a recoverable blip is re-issued before the loud failure. Kept small
# so a genuinely wedged runner is not retried for an unbounded stretch.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5  # exponential per retry: 0.5s, 1.0s, … between attempts

# Bounded re-resolution retry for a STALE actuation handle (BE-0289), held separate from BE-0207's
# transport retry above even though it starts at the same values: the two loops bound different
# things, so re-tuning one must not silently move the other. The re-query round-trip is the
# condition wait, not a fixed sleep; this backoff only spaces the attempts.
_STALE_MAX_ATTEMPTS = 3
# exponential per retry: 0.5s, 1.0s, … between re-resolve attempts
_STALE_BACKOFF_BASE_SECONDS = 0.5

# How long a mid-run crash-recovery (BE-0287) waits for a crashed runner to come back before failing
# loudly. A different concern from the transient retry above, which bounds a sub-second blip: a crash
# can leave the runner gone far longer than that as it relaunches, so this budget is generous enough to
# ride that out yet still bounded — a runner that is truly gone fails the run rather than hanging it.
_RECOVERY_TIMEOUT_SECONDS = 60

# How often the recovery wait re-asks the runner's liveness while it polls `/health` (BE-0360). The
# `/health` probe runs every 100ms, but the liveness check reads the runner's capture from a private
# offset, so asking it at the probe interval would cost 600 file reads across one window to learn of
# the death at most 900ms sooner — a difference that does not matter against a 60s window. Once a
# second bounds the wasted wait to about a second past the moment the death becomes observable.
_LIVENESS_POLL_SECONDS = 1.0

# How often `handle_system_alert` re-queries SpringBoard while waiting for the permission prompt to
# appear (BE-0316). A fixed inter-poll interval bounded by the step's own `timeout` — a condition
# wait, not a fixed up-front sleep: the loop returns the instant the alert's buttons are present.
_SYSTEM_ALERT_POLL_SECONDS = 0.2

# iOS reports every frame and coordinate in points, so that is the space stamped on this backend's
# actuation records.
_UNIT = "point"


def _as_float(value: Any) -> float | None:
    """A request body's numeric parameter as a float, or None when absent (for the actuation record)."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# How many *consecutive* mid-run crashes the recovery layer rides out before failing loudly. Recovery
# re-issues an idempotent call once the runner is back; a runner that crashes *again* on that re-issue
# used to propagate uncaught and fail the run on the second crash. Retrying the recovery a bounded
# number of times rides out a runner that flaps across a few calls, while a runner that never
# stabilizes still fails the run rather than looping forever. Each attempt re-uses `recovery_timeout`
# for its health wait, so the worst case stays bounded.
_MAX_CRASH_RECOVERIES = 3

# How many *consecutive* hung calls (the request reached the runner, no response ever came) the
# recovery loop rides out before calling the automation session wedged (BE-0354). Three: the original
# call plus two post-recovery re-issues. One healthy state shares the hang's surface — BE-0323
# serialized the runner's XCUITest operations while `/health` deliberately bypasses that
# serialization, so a long operation can hold the lock while a concurrent read times out — but a
# single lock-holder cannot span two post-recovery windows, because its own call fails its own retry
# ladder first. A read still hanging then is not flapping, it is wedged, and only the pipeline's
# device-level retry can help it, so the channel hands over instead of spending its remaining
# recovery cycles and their health waits proving the same dead end.
_MAX_HUNG_CALLS = 3


def _to_element(item: Mapping[str, Any]) -> base.Element:
    """Normalize one `GET /elements` item into an `Element`.

    The `handle` is dropped: it is a channel address, not a selector field, so matching is unaffected.
    """
    frame = item.get("frame") or (0.0, 0.0, 0.0, 0.0)
    return {
        "identifier": item.get("identifier"),
        "label": item.get("label"),
        "value": item.get("value"),
        "traits": list(item.get("traits") or []),
        "frame": (float(frame[0]), float(frame[1]), float(frame[2]), float(frame[3])),
        # The runner's reply carries no z signal; the in-app responder that would measure one is
        # BE-0355's still-open Unit 2, so this stays an honest absence rather than a derived guess.
        "nativeZ": None,
    }


def _decode(path: str, status_code: int, body: bytes) -> _Reply:
    """Decode a raw runner response into a `_Reply`.

    `/screenshot` returns raw PNG bytes; every other endpoint returns a small JSON object with a
    `status` (and, for `/elements`, an `elements` array). A non-200 still carries the server's
    `status` when present, so `not-found` / `stale` reach the driver as outcomes rather than as a
    transport error. Pure (no socket) so the wire format is unit-tested directly.
    """
    if path == "/screenshot":
        return _Reply(status=_OK if status_code == 200 else "error", png=body)
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise XcuitestChannelError(f"runner returned non-JSON for {path}: {body!r}") from exc
    status = data.get("status") or (_OK if status_code == 200 else "error")
    elements = data.get("elements")
    # `GET /screen` (BE-0326) carries the viewport as width/height; absent on every other endpoint.
    width, height = data.get("width"), data.get("height")
    size = (float(width), float(height)) if width is not None and height is not None else None
    return _Reply(status=str(status), elements=elements, size=size, raw=body)


class _TransportFailure(Exception):
    """A transport-level failure from one channel attempt, tagged with whether the request reached the runner.

    Internal to the retry seam (BE-0207): `_with_retry` reads `delivered` to decide whether re-issuing
    the call could double-apply a side-effecting write. It never escapes the module — an exhausted or
    retry-ineligible failure is turned into the caller-facing `XcuitestChannelError`.

    `hung` splits the delivered case by *how* it failed (BE-0354): a response timeout means the runner
    accepted the request and never answered, while a reset or a refused connection means it stopped
    serving. Only the former identifies a wedged automation session, so the two cannot share one tag.
    """

    def __init__(self, message: str, *, delivered: bool, hung: bool = False) -> None:
        super().__init__(message)
        self.delivered = delivered
        self.hung = hung


def _is_retry_eligible(method: str, *, delivered: bool) -> bool:
    """Whether a failed attempt is safe to re-issue (BE-0207, BE-0287).

    A failure before the request reached the runner is safe for any method — the runner never acted.
    Once the request was delivered, only idempotent reads may be retried; re-sending a side-effecting
    write after a response timeout could double-apply the action. Idempotency is keyed on the HTTP
    method: the runner's channel is REST-shaped, so every read is a `GET` (`/elements`, `/screenshot`,
    `/health`) and every actuation a `POST` — and the conservative direction is safe, since a request
    wrongly judged non-idempotent merely fails loudly instead of risking a double actuation.
    """
    return not delivered or method == "GET"


def _with_retry(inner: TransportFn, *, sleep: Callable[[float], None] = time.sleep) -> TransportFn:
    """Wrap *inner* with a bounded retry + exponential backoff over transient transport failures.

    Only a `_TransportFailure` is retried, and only when `_is_retry_eligible`; a decoded outcome
    (`stale` / `not-found`) is a `_Reply`, never an exception, so it is returned untouched and never
    retried — retrying an outcome would be the flakiness-by-absorption BE-0049 rejects. On exhaustion
    the loud `XcuitestRunnerCrashError` (a subclass of `XcuitestChannelError`) is raised, so the
    deterministic verdict is preserved: only a recoverable blip is absorbed. Each retry is logged, so a
    retried-then-passed run stays visible.
    """
    logger = logging.getLogger("bajutsu.xcuitest.channel")

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return inner(method, path, body)
            except _TransportFailure as exc:
                if attempt == _MAX_ATTEMPTS or not _is_retry_eligible(
                    method, delivered=exc.delivered
                ):
                    # A blip outlived the transient budget (or a delivered write cannot be re-issued):
                    # signal it as a crash, tagged so the BE-0287 recovery layer can decide whether the
                    # call is safe to re-issue. Still an XcuitestChannelError, so a bare `_with_retry`
                    # (no recovery wrapper) fails just as loudly as before.
                    raise XcuitestRunnerCrashError(
                        f"runner channel {method} {path} failed: {exc}",
                        method=method,
                        delivered=exc.delivered,
                        hung=exc.hung,
                    ) from exc
                logger.warning(
                    "runner channel %s %s failed (attempt %d/%d), retrying: %s",
                    method,
                    path,
                    attempt,
                    _MAX_ATTEMPTS,
                    exc,
                )
                sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        raise AssertionError(  # pragma: no cover - the loop returns or raises on every iteration
            "unreachable: the retry loop returns on success or raises on the final attempt"
        )

    return transport


class _HealthWait(Enum):
    """How a bounded `/health` wait ended (BE-0360).

    Two ways to fail deserve different diagnostics, and a caller told only "the wait failed" would
    have to re-ask the liveness callback to learn which, so the wait reports which end it reached.
    """

    READY = "ready"  # the runner answered `ready` within the budget
    TIMED_OUT = "timed-out"  # the deadline passed with no `ready`
    GONE = "gone"  # the liveness callback reported the runner unable to come back


def _await_health(
    transport: TransportFn,
    *,
    timeout: float,
    poll: float = 0.1,
    runner_alive: Callable[[], bool] | None = None,
    liveness_poll: float = _LIVENESS_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> _HealthWait:
    """Poll `GET /health` until the runner answers `ready`, reporting how the wait ended within *timeout*.

    A bounded condition wait (no fixed sleep that ignores the condition): `READY` the moment the runner
    is ready, `TIMED_OUT` if the deadline passes first. A channel failure while the runner is down is
    swallowed and re-polled, so "not accepting connections yet" reads as not-ready, not as an error.
    Shared by `await_ready` (startup) and the crash-recovery layer (mid-run), which differ only in the
    transport and timeout they poll with.

    `runner_alive`, when the caller supplies it, is re-asked while the wait runs and ends it with
    `GONE` as soon as it reports the runner unable to come back (BE-0360). The verdict is a fact that
    *changes* during the window — a crashing runner's `xcodebuild` exit and its suite's result line
    both follow the crash — so sampling it only before the wait would wait out the very failure the
    fast-fail exists for. It is asked *after* the `/health` probe, so a runner that answers `ready` on
    the same poll where its capture first shows the marker still counts as recovered: a runner that is
    serving is serving, whatever its log says. Absent (the startup caller, whose spawn retry owns its
    own liveness check), the wait is exactly what it was.
    """
    start = clock()
    deadline = start + timeout
    # The crash-recovery caller asks the same question immediately before this wait, so the first
    # in-loop ask is scheduled one interval out rather than duplicating it on the very first poll.
    next_liveness = start + liveness_poll
    while True:
        try:
            if transport("GET", "/health", None).status == "ready":
                return _HealthWait.READY
        except (XcuitestChannelError, _TransportFailure):
            pass  # runner not accepting connections yet; keep probing until the deadline
        now = clock()
        if runner_alive is not None and now >= next_liveness:
            next_liveness = now + liveness_poll
            if not runner_alive():
                return _HealthWait.GONE
        if now >= deadline:
            return _HealthWait.TIMED_OUT
        sleep(poll)


def _observe_stall(hook: Callable[[], None], logger: logging.Logger) -> None:
    """Let the diagnostics hook look at a declared crash, absorbing whatever it does (BE-0361).

    A capture runs on the failure path it documents, so a broken hook must cost that path a log line
    and nothing else — never the exception that would replace the crash diagnostic the caller is
    about to raise.

    The hook takes no argument: naming the trigger (and therefore the directory the capture writes) is
    the environment's business, not the channel's, so nothing the channel passes can reach a path.
    """
    try:
        hook()
    except Exception:
        # With the traceback: a capture that regresses would otherwise log the same opaque line on
        # every CI failure, leaving the diagnostics themselves undiagnosable.
        logger.warning(
            "stall diagnostics hook failed; the crash diagnosis is unaffected", exc_info=True
        )


def _runner_gone_mid_run(
    method: str, path: str, crash: XcuitestRunnerCrashError
) -> XcuitestRunnerCrashError:
    """The crash diagnostic for a runner that will never answer on its port again.

    One wording for two moments the same fact can be observed: the runner was already gone when the
    crash was declared, or its death became observable while the recovery wait ran (BE-0360).
    """
    return XcuitestRunnerCrashError(
        f"runner channel {method} {path} failed: the runner is gone mid-run — its process exited or "
        "its test run already ended (it will not recover on this port)",
        method=method,
        delivered=crash.delivered,
    )


def _with_crash_recovery(
    inner: TransportFn,
    *,
    health: Callable[[float], _HealthWait],
    runner_alive: Callable[[], bool] | None = None,
    on_stall: Callable[[], None] | None = None,
    recovery_timeout: float = _RECOVERY_TIMEOUT_SECONDS,
    max_recoveries: int = _MAX_CRASH_RECOVERIES,
    max_hung_calls: int = _MAX_HUNG_CALLS,
) -> TransportFn:
    """Wrap *inner* so a mid-run runner crash surfaces deterministically, not as a lost gesture (BE-0287).

    The BE-0207 retry seam (*inner*) absorbs a sub-second blip; a crash outlives its budget and raises
    `XcuitestRunnerCrashError`. This layer catches that and decides by the same `delivered` split the
    seam already draws. An idempotent read — or a write that never reached the runner — waits for the
    runner to come back (via *health*, the bounded `/health` poll) and re-issues, because re-reading is
    safe. The re-issue is itself protected: a runner that crashes *again* on the re-issued call is
    recovered anew, up to `max_recoveries` consecutive crashes, so a flapping runner is ridden out
    instead of failing the run on the second crash. A write that may already have been delivered is
    never re-sent (double-actuation risk) and fails with a distinct crash diagnostic, so the run
    stops on an honest "the runner died
    mid-gesture" rather than a misleading `actual='idle'`. Every crash — recovered or not — is logged as
    visibly as the retry seam logs a retried blip (BE-0287 Unit 4), so a crashed-and-recovered run is
    never indistinguishable from one that never crashed.

    `/health` itself passes straight through: it is the probe recovery leans on, so wrapping it would
    recurse (and block a startup `await_ready` for the whole recovery window on a runner not yet up).

    A *wedged automation session* is split out of that flap-riding (BE-0354): when the same call keeps
    **hanging** — reaching the runner and never being answered — across `max_hung_calls` consecutive
    crashes while `/health` keeps replying, the runner's HTTP server is fine and the machinery behind
    it is not. No amount of re-issuing can fix that, so the crash is raised at once with its own
    diagnostic and the pipeline's device-level retry takes over. A connection-level failure (refused,
    or reset mid-response) is the genuinely crashing runner this loop was built for and keeps riding.

    `runner_alive` splits recovery on whether the runner can still come back: when the environment
    supplies its liveness check and it reports the runner **gone** — the `xcodebuild` process exited,
    or its XCTest run already ended and left the parent lingering (BE-0354) — nothing will answer
    `/health` on this port again (nothing respawns it mid-recovery), so recovery fails fast instead of
    polling the dead port for the whole window — the pipeline's crash recovery then leases a fresh
    device and re-runs the scenario. Absent (a test fake) or reporting the runner alive, it changes
    nothing: an alive-but-unreachable runner stays BE-0287's recoverable case and waits out *health*.

    That question is asked twice over: here, before the wait, and again by *health* while the wait runs
    (BE-0360). Sampling it only here would catch just the runner already gone when the crash was
    declared, while the ordinary mid-run crash — whose `xcodebuild` exit and suite result line both
    follow it — became observable a moment later and was waited out in full. A wait that ends on that
    verdict is reported with the same "gone" diagnostic as the early exit, not as a window waited out.

    `on_stall`, when the environment supplies it, is called once per declared crash — before recovery
    decides anything, so ahead of both liveness samples above — and a bounded capture of the Simulator
    and host state therefore runs while that state still exists (BE-0361). It is an observer: its own
    failure is swallowed, and neither it nor anything it returns reaches the crash verdict below.
    """
    logger = logging.getLogger("bajutsu.xcuitest.channel")

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/health":
            return inner(method, path, body)
        recoveries = 0
        hangs = 0
        while True:
            try:
                return inner(method, path, body)
            except XcuitestRunnerCrashError as crash:
                # Consecutive, so a single hang between two connection-level crashes never accrues
                # toward the wedge verdict — only a call that keeps being accepted and never answered.
                hangs = hangs + 1 if crash.hung else 0
                logger.warning(
                    "runner channel %s %s: the runner became unreachable past the retry budget — a mid-run crash: %s",
                    method,
                    path,
                    crash,
                )
                if on_stall is not None:
                    _observe_stall(on_stall, logger)
                if not _is_retry_eligible(method, delivered=crash.delivered):
                    raise XcuitestRunnerCrashError(
                        f"runner channel {method} {path} failed after delivery: the runner did not confirm "
                        "the write, which may have been lost and cannot be safely re-applied (mid-run crash)",
                        method=method,
                        delivered=crash.delivered,
                    ) from crash
                if hangs >= max_hung_calls:
                    # The runner accepted this call `hangs` times and answered none while `/health`
                    # kept replying: its automation session is wedged, not flapping. Only a
                    # device-level remedy can clear that, so hand over now instead of spending the
                    # remaining recovery cycles — each a timeout ladder plus a health wait — on it.
                    raise XcuitestRunnerCrashError(
                        f"runner channel {method} {path} failed: the call reached the runner and hung "
                        f"{hangs} times while /health kept answering — a wedged automation session, "
                        "which no re-issue can clear (mid-run crash)",
                        method=method,
                        delivered=crash.delivered,
                        hung=True,
                    ) from crash
                recoveries += 1
                if recoveries > max_recoveries:
                    # The runner keeps crashing on each re-issue: it is not a single flake but a runner
                    # that never stabilizes, so fail loudly rather than loop. Distinct from the
                    # "did not recover" (health never came back) diagnostic below.
                    raise XcuitestRunnerCrashError(
                        f"runner channel {method} {path} failed: the runner crashed {recoveries} times and "
                        f"stayed unstable past the {max_recoveries}-recovery budget (mid-run crash)",
                        method=method,
                        delivered=crash.delivered,
                    ) from crash
                if runner_alive is not None and not runner_alive():
                    # The runner is gone for good — its process exited, or its XCTest run ended and
                    # only the `xcodebuild` parent lingers (BE-0354). Either way nothing will answer
                    # `/health` on this port again, so polling the recovery window would only wait out
                    # an inevitable failure. Fail fast with a distinct diagnostic; the pipeline's crash
                    # recovery then leases a fresh device and re-runs the scenario. A runner merely
                    # unreachable (alive) skips this and waits out `health` below, so BE-0287's
                    # recoverable case is unchanged. Kept as an early exit even though *health* now
                    # re-asks the same question (BE-0360): it costs one call, and it spares a runner
                    # already gone here a poll interval it does not need.
                    raise _runner_gone_mid_run(method, path, crash) from crash
                waited = health(recovery_timeout)
                if waited is _HealthWait.GONE:
                    # The same death, observed a moment later: the `xcodebuild` exit and the suite's
                    # result line both *follow* the crash, so the ordinary mid-run crash becomes
                    # observable during the wait rather than before it (BE-0360). Report it the way a
                    # runner found gone before the wait is reported, not as a window waited out.
                    raise _runner_gone_mid_run(method, path, crash) from crash
                if waited is not _HealthWait.READY:
                    raise XcuitestRunnerCrashError(
                        f"runner channel {method} {path} failed: the runner crashed mid-run and did not "
                        f"recover within {recovery_timeout}s",
                        method=method,
                        delivered=crash.delivered,
                    ) from crash
                logger.warning(
                    "runner channel %s %s: the runner recovered from a mid-run crash; re-issuing the "
                    "idempotent call (recovery %d/%d)",
                    method,
                    path,
                    recoveries,
                    max_recoveries,
                )
                # Loop to re-issue. A re-issue that crashes again is caught here and recovered anew, up
                # to max_recoveries, so a runner that flaps across consecutive calls is ridden out
                # instead of failing the run on the second crash. `_is_retry_eligible` still gates every
                # attempt, so a delivered write is never re-sent even once.

    return transport


def _raw_http_transport(host: str, port: int) -> TransportFn:
    """One HTTP attempt to the runner's loopback server, tagging failures for the retry seam (BE-0207).

    A failure while *connecting* means the request never reached the runner (`delivered` stays
    `False`); once the socket is open, any later failure — a partial send or a response-side timeout —
    may have reached the runner (`delivered` is `True`). `_with_retry` and the BE-0287 crash-recovery
    use that split to decide what is safe to re-issue, so the flip is deliberately conservative: a
    write whose bytes may have started reaching the runner is never re-sent (a double-actuation risk),
    it fails loudly instead.
    """

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        # One `app.snapshot()` per `/elements` (BE-0105), so the bounded read window still covers a
        # cold first snapshot; a write gets the longer actuation window (`_timeout_for`) since it
        # can't be retried after delivery — both still fail a wedged runner in a reasonable window.
        conn = http.client.HTTPConnection(host, port, timeout=_timeout_for(method))
        delivered = False
        try:  # pragma: no cover - exercised on-device against the real runner, not on the gate
            conn.connect()  # split from send: a connect failure is safe to re-issue, a send failure isn't
            delivered = (
                True  # the socket is open; a later send/read failure may have reached the runner
            )
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload is not None else {}
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            return _decode(path, resp.status, resp.read())
        except OSError as exc:  # pragma: no cover - see above
            # A socket timeout on an open connection is the hang BE-0354 keys on; a refused connect
            # or a reset mid-response is the runner going away, which recovery still rides out.
            raise _TransportFailure(
                str(exc), delivered=delivered, hung=delivered and isinstance(exc, TimeoutError)
            ) from exc
        finally:
            conn.close()

    return transport


def _http_transport(
    host: str,
    port: int,
    *,
    runner_alive: Callable[[], bool] | None = None,
    on_stall: Callable[[], None] | None = None,
) -> tuple[TransportFn, TransportFn]:
    """The real transport, plus the raw single-attempt transport used for fast health probes.

    Two layers over the raw socket: BE-0207's `_with_retry` smooths a sub-second blip, and BE-0287's
    `_with_crash_recovery` rides out a mid-run crash (idempotent re-issue) or fails loudly on a write it
    must not re-send. Both the crash-recovery health poll and the cold-spawn liveness probe
    (`XcuitestDriver.health_ready`, BE-0319) need probing to stay fast, not retried: `_with_retry`
    re-issues a down connection up to `_MAX_ATTEMPTS` times with backoff, so routing a "single-shot"
    probe through it would silently cost over a second per call instead of one quick attempt — the raw
    transport is returned alongside the wrapped one so both callers can reuse this same instance.

    `runner_alive`, when the environment supplies its liveness check, lets crash-recovery fail fast on
    a runner that cannot come back — its process exited, or its XCTest run already ended (BE-0354) —
    rather than polling the dead port for the whole recovery window. It reaches both places that ask
    the question: the check before the wait, and the wait itself, which re-asks it as the window runs
    (BE-0360). Absent, recovery is exactly BE-0287's. `on_stall` is the environment's bounded
    diagnostics capture (BE-0361), an observer of the same crash declaration. Both are keyword-only,
    so two adjacent optional callbacks cannot be swapped at a call site.
    """
    raw = _raw_http_transport(host, port)
    wrapped = _with_crash_recovery(
        _with_retry(raw),
        health=lambda timeout: _await_health(raw, timeout=timeout, runner_alive=runner_alive),
        runner_alive=runner_alive,
        on_stall=on_stall,
    )
    return wrapped, raw


class XcuitestDriver:
    """Driver for the iOS Simulator via a resident XCUITest runner (semantic, identifier-based)."""

    name = "xcuitest"

    # Capabilities: a semantic tap (by handle, no coordinates), native condition waiting, and
    # two-finger gestures. No NETWORK — network evidence comes from
    # the app-side collector (BE-0020 boundary), not the actuator. The whole device-control family
    # (`DEVICE_CONTROL_ALL`) and the permission grants because xcuitest shares the iOS Simulator
    # lifecycle, which wires a real simctl-backed `DeviceControl` for its runs too (BE-0128;
    # per-operation tokens since BE-0212). This is the *static* set; a real device (`deviceType:
    # device`) drops the simctl-backed capabilities at run time via `backends.capabilities_for_run`,
    # since simctl reaches only the Simulator (BE-0238). A class constant so the preflight (BE-0082)
    # reads it via backends.capabilities_for without constructing a driver.
    CAPABILITIES = (
        frozenset(
            {
                base.Capability.QUERY,
                base.Capability.ELEMENTS,
                base.Capability.SCREENSHOT,
                base.Capability.SEMANTIC_TAP,
                base.Capability.CONDITION_WAIT,
                base.Capability.MULTI_TOUCH,
                base.Capability.TEXT_SELECTION,
                base.Capability.HANDLE_SYSTEM_ALERT,
            }
        )
        | base.DEVICE_CONTROL_ALL
        | base.IOS_PERMISSION_CAPABILITIES
    )

    def __init__(
        self,
        *,
        transport: TransportFn | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        runner_alive: Callable[[], bool] | None = None,
        on_stall: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        device_os: DeviceOS | None = None,
    ) -> None:
        # The parsed OS version of the device this drives (BE-0358), or None when the environment
        # could not name one. Nothing here branches on it yet — it exists so a driver-level failure
        # can name the OS it happened on, and so the first genuinely per-OS decision has one route
        # to read. Set per construction, not per lease, so it follows a mid-run device replacement
        # instead of going stale.
        self.device_os = device_os
        if transport is not None:
            # A test fake serves both roles: it has no BE-0207 retry to distinguish away.
            self._transport = transport
            self._probe_transport = transport
        else:
            # `runner_alive` lets crash-recovery fail fast on a runner that cannot come back; the
            # environment supplies its liveness check, None keeps BE-0287's recovery. `on_stall` is
            # the environment's diagnostics capture (BE-0361), an observer of the same crash
            # declaration.
            self._transport, self._probe_transport = _http_transport(
                host, port, runner_alive=runner_alive, on_stall=on_stall
            )
        # Injectable so the stale re-resolution backoff (BE-0289) adds no wall time under test.
        self._sleep = sleep
        # The device screen size (BE-0326), fetched once from the runner; fixed for a run.
        self._screen: base.Point | None = None
        # What this driver actually actuated, drained per step by the run loop.
        self._actuations = ActuationLog()
        # The raw `GET /elements` body behind the last query (`base.RawSourceProvider`, the `rawTree`
        # capture kind), kept undecoded: `last_raw_source()` is read only on the rare step that actually
        # requests `rawTree` capture, so decoding here on every query — the common, capture-off case —
        # would be pure waste. None until the first read. No `parsed_input`: unlike adb's resident
        # channel, nothing here narrows the runner's own reply before it becomes `elements`.
        self._raw_bytes: bytes | None = None

    # --- the channel ---

    def _query_with_handles(self) -> tuple[list[base.Element], dict[int, str]]:
        """A snapshot plus a map from each element's object identity to its handle.

        Keyed by `id()` of the returned dicts: `resolve_unique` returns one of these very objects, so
        the resolved element's handle is an O(1) identity lookup — the element is acted on by the
        exact handle the runner minted for it, never re-resolved on the runner side.
        """
        reply = self._transport("GET", "/elements", None)
        self._raw_bytes = reply.raw
        return self._parse_elements(reply)

    @staticmethod
    def _parse_elements(reply: _Reply) -> tuple[list[base.Element], dict[int, str]]:
        """Turn a runner element reply into elements plus an identity→handle map (BE-0105).

        Shared by the app-tree query (`/elements`) and the SpringBoard alert query
        (`/systemAlert/query`, BE-0316): both mint a handle per element the same way, so both feed
        `resolve_unique` and then act by the exact handle the runner minted.
        """
        elements: list[base.Element] = []
        handles: dict[int, str] = {}
        for item in reply.elements or []:
            handle = item.get("handle")
            if not handle:  # a missing handle is a malformed response, not a coercible empty string
                raise XcuitestChannelError(f"runner returned an element without a handle: {item!r}")
            el = _to_element(item)
            elements.append(el)
            handles[id(el)] = str(handle)
        return elements, handles

    def _resolve_handle(self, sel: base.Selector) -> tuple[str, base.Element]:
        """Resolve *sel* to a unique element Python-side; return its snapshot handle and the element.

        The element travels with the handle so the caller can record what it actuated without paying a
        second `/elements` round trip for the same resolution.

        Raises:
            ElementNotFound: Nothing matched.
            AmbiguousSelector: Several elements matched, with no `index` to disambiguate. Both are
                raised before any actuation request is sent.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        return handles[id(el)], el

    def _actuate(
        self,
        path: str,
        body: Mapping[str, Any],
        sel: base.Selector,
        *,
        gesture: str,
        element: base.Element,
        substitution: str | None = None,
    ) -> None:
        # A `stale` reply means the handle no longer maps to a live element, from one of two
        # pre-actuation points: the runner's `store.lookup` returns `stale` before touching anything
        # when the screen re-snapshotted, or the interaction itself raised an element-resolution
        # failure ("No matches found") that the runner catches and reports as `stale`
        # (Router.onMainCatching). Both precede event synthesis — XCUITest resolves the element, and
        # raises if it is gone, *before* it synthesizes the tap/gesture — so re-issuing cannot
        # double-actuate. Re-query and re-actuate while the same selector still resolves uniquely
        # (BE-0289). Zero/many matches raise ElementNotFound / AmbiguousSelector out of
        # `_resolve_handle` and fail immediately, spending no further attempts.
        request: dict[str, Any] = dict(body)
        for attempt in range(1, _STALE_MAX_ATTEMPTS + 1):
            # Recorded per attempt, before the transport answers: a step that failed to actuate still
            # shows what it aimed at, and a stale-retried gesture shows both the element that went
            # stale and the one that was finally actuated. `points` stays empty — the runner picks the
            # touch point on the far side of the handle, so the driver has no coordinate to state.
            self._actuations.record(
                Actuation(
                    gesture=gesture,
                    via="handle",
                    unit=_UNIT,
                    frame=element["frame"],
                    target=element["identifier"],
                    duration_s=_as_float(body.get("duration")),
                    scale=_as_float(body.get("scale")),
                    radians=_as_float(body.get("radians")),
                    substitution=substitution,
                )
            )
            reply = self._transport("POST", path, request)
            # Stamp the attempt just recorded with the runner's answer, so a stale-retried gesture
            # does not leave several identical records with nothing saying which one landed.
            self._actuations.settle(reply.status == _OK)
            if reply.status == _OK:
                return
            if reply.status == _STALE:
                if attempt == _STALE_MAX_ATTEMPTS:
                    raise base.ElementNotFound(f"element vanished (stale handle): {sel!r}")
                self._sleep(_STALE_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
                request["handle"], element = self._resolve_handle(sel)
                continue
            if reply.status == _NOT_FOUND:
                raise base.ElementNotFound(f"no actuatable element for: {sel!r}")
            if reply.status == _NOT_HITTABLE:
                # Distinct from `_STALE`: the element is live and correctly resolved, but XCTest's own
                # `isHittable` refuses it (covered by another element, or offscreen) — not a race to
                # retry here, so it surfaces once as a tappability failure. Any bounded recovery
                # (a scroll) happens above the driver, in the orchestrator.
                raise base.ElementNotTappable(f"element resolved but not hittable: {sel!r}")
            # Any other status (e.g. an "error" from a 500 / malformed response) is a runner failure,
            # not a test outcome — fail loudly rather than masking it as element-not-found.
            raise XcuitestChannelError(
                f"runner error actuating {path} (status={reply.status}): {sel!r}"
            )

    # --- Driver Protocol ---

    def drain_actuations(self) -> Drained:
        """The concrete actuations performed since the last drain (`ActuationReporter`)."""
        return self._actuations.drain()

    def last_raw_source(self) -> base.RawSource | None:
        """The raw `GET /elements` body behind the last query (`base.RawSourceProvider`).

        Decoded here, not at query time: this is read only on the rare step that requests `rawTree`
        capture, so paying the decode on every query — the common, capture-off case — would be waste.
        """
        if self._raw_bytes is None:
            return None
        return base.RawSource(text=self._raw_bytes.decode("utf-8"), suffix=".json")

    def query(self) -> list[base.Element]:
        elements, _ = self._query_with_handles()
        return elements

    def tap(self, sel: base.Selector) -> None:
        handle, el = self._resolve_handle(sel)
        try:
            self._actuate("/tap", {"handle": handle}, sel, gesture="tap", element=el)
        except base.ElementNotTappable as refused:
            self._tap_sole_reachable_descendant(sel, refused)

    def _tap_sole_reachable_descendant(
        self, sel: base.Selector, refused: base.ElementNotTappable
    ) -> None:
        """Tap the one reachable named descendant of a refused target, or re-raise naming the rest.

        iOS can report a container inflated over the control it wraps — a SwiftUI `Stepper` whose
        accessibility element spans its whole form row — and refuse a tap on the container while the
        control inside it is perfectly reachable. Where exactly one named descendant is reachable,
        there is no choice to make and the tap goes there. Where none or several are, there is a
        choice, so this re-raises rather than making it: an author cannot predict which of two
        equally reachable children a driver would pick, and picking one anyway is the guess prime
        directive 2 forbids. The message then names the candidates, so the author can select one
        directly instead of reading "not hittable" about an element plainly on screen.

        Scoped to `tap` on purpose. A long-press or a two-finger gesture redirected to a child is a
        different intent, not the same intent reaching its target.

        Raises:
            ElementNotTappable: Chained from *refused*, so the original refusal stays the cause.
        """
        elements, handles = self._query_with_handles()
        # Re-resolved from a fresh tree rather than reusing the refused element: the refusal may have
        # been the first sign of a screen still settling, and a candidate list read off a stale
        # snapshot could offer an element that has since moved out of the container.
        target = base.resolve_unique(elements, sel)
        candidates = base.redirect_candidates(elements, target)
        if not candidates or len(candidates) > base.MAX_REDIRECT_CANDIDATES:
            raise refused
        # `redirect_candidates` only ever returns named elements, so the identifier is never None
        # here; binding it in the comprehension is what lets the selector below stay typed.
        reachable = [
            (el, name)
            for el in candidates
            if (name := el["identifier"]) is not None and self._is_hittable(handles[id(el)])
        ]
        if len(reachable) != 1:
            named = ", ".join(repr(el["identifier"]) for el in candidates)
            raise base.ElementNotTappable(
                f"element resolved but not hittable: {sel!r} — "
                f"{len(reachable)} of its {len(candidates)} named descendants are reachable "
                f"({named}), so none of them is the one this tap meant"
            ) from refused
        child, child_id = reachable[0]
        # Actuated by the child's own id, not the container's: `_actuate` re-resolves from `sel` on a
        # stale retry, and passing the container's selector would silently undo the redirect there.
        self._actuate(
            "/tap",
            {"handle": handles[id(child)]},
            {"id": child_id},
            gesture="tap",
            element=child,
            substitution="soleHittableDescendant",
        )

    def _is_hittable(self, handle: str) -> bool:
        """Whether the runner reports this handle reachable right now — the probe behind the redirect.

        Unlike `is_tappable`, this takes a handle already resolved from the caller's own snapshot, so
        the two never disagree about which element was asked about. A `stale` or `not-found` reply
        reads as unreachable: the candidate came from the very query this handle did, so either answer
        means the screen moved under the probe and the offer is no longer one to make.
        """
        reply = self._transport("POST", "/isHittable", {"handle": handle})
        if reply.status == _OK:
            return True
        if reply.status in (_STALE, _NOT_FOUND, _NOT_HITTABLE):
            return False
        raise XcuitestChannelError(f"runner error checking isHittable (status={reply.status})")

    def is_tappable(self, sel: base.Selector) -> bool:
        """Whether `sel` resolves to a unique element XCTest's own `isHittable` reports as reachable.

        A pure query, unlike `tap`/`double_tap`/`long_press`: it never actuates and never retries a
        `stale` reply, so a caller (the scroll-recovery loop) can call it repeatedly with no side
        effects, resolving fresh from the current screen every time. Not found means "not tappable"
        (`False`), matching every other backend's convention for a target not yet in the tree, rather
        than propagating; an ambiguous selector still raises `AmbiguousSelector` immediately, since
        occlusion is a different question from selector ambiguity. A `stale` handle (the screen
        changed between resolving and asking) also reads as `False` rather than retried, since the
        caller re-resolves fresh on its own next call. Any other status is a genuine runner/channel
        problem, not a test outcome, and raises loudly (`XcuitestChannelError`) exactly as `_actuate`
        does for the same class of reply, rather than being folded into a misleadingly clean `False`.
        """
        try:
            handle, _el = self._resolve_handle(sel)
        except base.ElementNotFound:
            return False
        reply = self._transport("POST", "/isHittable", {"handle": handle})
        if reply.status == _OK:
            return True
        if reply.status in (_STALE, _NOT_FOUND, _NOT_HITTABLE):
            return False
        raise XcuitestChannelError(
            f"runner error checking isHittable (status={reply.status}): {sel!r}"
        )

    def double_tap(self, sel: base.Selector) -> None:
        handle, el = self._resolve_handle(sel)
        self._actuate("/tap", {"handle": handle, "taps": 2}, sel, gesture="doubleTap", element=el)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        handle, el = self._resolve_handle(sel)
        self._actuate(
            "/tap",
            {"handle": handle, "duration": duration},
            sel,
            gesture="longPress",
            element=el,
        )

    def tap_point(self, p: base.Point) -> None:
        # A raw coordinate tap (system alerts and the like), the one path with no element/handle.
        self._actuations.record(Actuation(gesture="tap", via="coordinate", unit=_UNIT, points=(p,)))
        reply = self._transport("POST", "/tap", {"point": [p[0], p[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"coordinate tap failed ({reply.status}) at {p}")

    def pinch(self, sel: base.Selector, scale: float) -> None:
        handle, el = self._resolve_handle(sel)
        self._actuate(
            "/gesture",
            {"handle": handle, "kind": "pinch", "scale": scale},
            sel,
            gesture="pinch",
            element=el,
        )

    def rotate(self, sel: base.Selector, radians: float) -> None:
        handle, el = self._resolve_handle(sel)
        self._actuate(
            "/gesture",
            {"handle": handle, "kind": "rotate", "radians": radians},
            sel,
            gesture="rotate",
            element=el,
        )

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        self._actuations.record(
            Actuation(gesture="swipe", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        reply = self._transport("POST", "/swipe", {"from": [frm[0], frm[1]], "to": [to[0], to[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"swipe failed ({reply.status})")

    def viewport(self) -> base.Point:
        # The device screen size from the resident runner (BE-0326). The flattened element tree
        # excludes the app window and can hold buffered off-screen ScrollView children, so
        # `screen_size_from_elements` overshoots the screen — a `scroll` stop condition off that would
        # judge an off-screen center as on-screen and drive the gesture off-screen. Cached for the run.
        if self._screen is None:
            reply = self._transport("GET", "/screen", None)
            if reply.status != _OK or reply.size is None:
                raise XcuitestChannelError(f"screen size unavailable ({reply.status})")
            self._screen = reply.size
        return self._screen

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A non-inertial scroll (BE-0326): the resident runner's `/scroll` holds the drag at its end
        # before lifting, so the scroll view settles where the gesture left it rather than flinging
        # past the target — the contract the `scroll` action's bounded re-query loop relies on. A
        # plain `/swipe` lifts with residual velocity, so iOS carries the content onward.
        self._actuations.record(
            Actuation(gesture="scroll", via="coordinate", unit=_UNIT, points=(frm, to))
        )
        reply = self._transport("POST", "/scroll", {"from": [frm[0], frm[1]], "to": [to[0], to[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"scroll failed ({reply.status})")

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; iOS ネイティブに <select> はない"
        )

    def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
        # Tap a SpringBoard permission-prompt button deterministically (BE-0316). The alert is
        # out-of-process, so the runner queries a second, on-demand `XCUIApplication` for
        # `com.apple.springboard` and mints a handle per alert button, exactly as it does for the
        # app's own tree. Resolution stays Python-side in `resolve_unique`, so the same zero /
        # ambiguous / index discipline every selector follows decides which button is tapped — no
        # screenshot, no vision model. Poll the query to a deadline: the prompt only appears once the
        # app makes the request, so a condition wait (no fixed sleep) waits it in without a bound guess.
        deadline = time.monotonic() + timeout
        while True:
            buttons, handles = self._parse_elements(
                self._transport("POST", "/systemAlert/query", {})
            )
            if buttons:
                break
            if time.monotonic() >= deadline:
                raise base.ElementNotFound(f"no system alert appeared within {timeout}s: {sel!r}")
            self._sleep(_SYSTEM_ALERT_POLL_SECONDS)
        el = base.resolve_unique(buttons, sel)
        # Handle-based like every other actuation here, and out of the app's own coordinate space, so
        # no point. `target` is usually unset: a SpringBoard button is addressed by visible label and
        # generally carries no identifier, and the label is a redaction risk the record won't take.
        self._actuations.record(
            Actuation(
                gesture="systemAlert",
                via="handle",
                unit=_UNIT,
                frame=el["frame"],
                target=el["identifier"],
            )
        )
        reply = self._transport("POST", "/systemAlert/tap", {"handle": handles[id(el)]})
        if reply.status != _OK:
            # The alert vanished between query and tap (dismissed itself, or the button moved off).
            raise base.ElementNotFound(
                f"system alert button vanished before tap (status={reply.status}): {sel!r}"
            )

    def system_alert_labels(self) -> list[str]:
        """The current SpringBoard alert's button labels, or [] when none is up (BE-0315).

        A single, non-blocking read reusing BE-0316's `/systemAlert/query` (the same route
        `handle_system_alert` polls) — the reactive guard reads it to decide whether a prompt is
        showing and which button its policy should tap. Unlabeled buttons are dropped: the policy
        resolves by visible label.
        """
        buttons, _ = self._parse_elements(self._transport("POST", "/systemAlert/query", {}))
        return [label for b in buttons if (label := b["label"])]

    def back(self) -> None:
        # iOS has no hardware back: tap the OS navigation back button. Reuses `tap` rather than
        # re-issuing the actuate call (BE-0210).
        self.tap({"id": base.OS_BACK_BUTTON})

    def type_text(self, text: str) -> None:
        # `text` is deliberately absent from the record — not even its length (see `actuation.py`).
        self._actuations.record(Actuation(gesture="typeText", via="focused", unit=_UNIT))
        reply = self._transport("POST", "/type", {"text": text})
        if reply.status != _OK:
            raise XcuitestChannelError(f"type failed ({reply.status})")

    def delete_text(self, count: int) -> None:
        # A run of backspaces on the focused field (BE-0265); XCUIElement types the delete key natively.
        self._actuations.record(Actuation(gesture="deleteText", via="focused", unit=_UNIT))
        reply = self._transport("POST", "/deleteText", {"count": count})
        if reply.status != _OK:
            raise XcuitestChannelError(f"deleteText failed ({reply.status})")

    def select_all(self) -> None:
        self._actuations.record(Actuation(gesture="selectAll", via="focused", unit=_UNIT))
        reply = self._transport("POST", "/selectAll", {})
        if reply.status != _OK:
            raise XcuitestChannelError(f"selectAll failed ({reply.status})")

    def copy_selection(self) -> None:
        self._actuations.record(Actuation(gesture="copy", via="focused", unit=_UNIT))
        reply = self._transport("POST", "/copy", {})
        if reply.status != _OK:
            raise XcuitestChannelError(f"copy failed ({reply.status})")

    def wait_for(self, sel: base.Selector) -> bool:
        """Single-shot: whether `sel` matches the current screen (BE-0118).

        Delegates to the shared `base.default_wait_for` so the four backends share one body; the
        deadline poll lives in `base.wait_until`, so the timeout is honoured identically (BE-0251).
        """
        return base.default_wait_for(self, sel)

    def screenshot(self, path: str) -> None:
        reply = self._transport("GET", "/screenshot", None)
        if reply.status != _OK or reply.png is None:
            # Fail loudly rather than writing an empty / non-PNG artifact on a runner error.
            raise XcuitestChannelError(f"screenshot failed (status={reply.status})")
        Path(path).write_bytes(reply.png)

    def capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

    # --- lifecycle ---

    def await_ready(self, timeout: float = 10.0, poll: float = 0.1) -> None:
        """Block until the runner's loopback server answers `GET /health` with `ready`.

        A bounded condition wait: it polls `/health` (no fixed sleep that ignores the condition) and
        fails loudly (`XcuitestChannelError`) on timeout rather than hanging, so "the runner never
        came up" is a clear run failure.
        """
        if _await_health(self._transport, timeout=timeout, poll=poll) is not _HealthWait.READY:
            raise XcuitestChannelError(
                f"xcuitest runner did not come up within {timeout}s (health never ready)"
            )

    def health_ready(self) -> bool:
        """One `GET /health` probe: `True` if the runner answers `ready`, `False` if not up yet (BE-0319).

        A single non-blocking check (a zero-budget `_await_health`: it probes once and returns),
        unlike `await_ready`'s bounded poll loop. Uses `_probe_transport` — the raw, single-attempt
        transport, the same one the crash-recovery health poll uses — rather than `_transport`, whose
        BE-0207 retry would silently turn a "single probe" into up to `_MAX_ATTEMPTS` attempts with
        backoff (over a second) each call; the cold-spawn liveness wait that watches the `xcodebuild`
        process between probes needs each one fast, since it owns its own loop and timing. Reuses the
        driver's one definition of the health-wire contract — the endpoint, the `ready` sentinel, and
        which transport errors read as not-ready — rather than restating it.
        """
        return _await_health(self._probe_transport, timeout=0.0) is _HealthWait.READY
