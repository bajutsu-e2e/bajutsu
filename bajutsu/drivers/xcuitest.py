"""XCUITest backend — semantic actuation over a loopback HTTP channel (BE-0019).

Unlike idb (a subprocess CLI that taps frame-centre coordinates), XCUITest actuates from a resident
XCTest runner living on the Simulator, so Python and that runner talk over a small `127.0.0.1`
channel — the same loopback pattern `network.py` already uses, in the Python→runner direction. This
module is the **Python side** of that channel: it builds the requests, parses the responses, and maps
failures onto the shared `Driver` exceptions. The runner itself (a generic XCTest target in
`BajutsuKit`) is a separate, on-device slice; here the transport is injectable so the request/response
logic is exercised against a fake — no Simulator on the gate.

The crux is **element addressing**: resolution stays Python-side (`resolve_unique`), so the driver
acts on exactly the element it resolved by sending that element's opaque *per-snapshot handle* the
runner minted — never a re-resolved predicate that could match a different element. A handle can go
stale when the screen re-snapshots between resolve and actuate (a freshly foregrounded screen still
settling), so a `stale` reply is treated as a trigger to re-query rather than an immediate failure
(BE-0289): the actuation is re-issued only while the same selector still resolves Python-side to a
single element, and fails loudly the moment it resolves to none (`ElementNotFound`) or many
(`AmbiguousSelector`) — so the retry tolerates a snapshot race without ever absorbing a real
disappearance.

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
from pathlib import Path
from typing import Any

from bajutsu.drivers import base


class XcuitestChannelError(RuntimeError):
    """The runner channel failed: it never came up, stopped answering, or returned a bad response.

    An infrastructure failure, kept distinct from a test outcome — a crashed/absent runner fails the
    run loudly rather than being read as "element not found".
    """


class XcuitestRunnerCrashError(XcuitestChannelError):
    """The runner died mid-run: the loopback channel stayed unreachable past the transient-retry budget (BE-0287).

    A crash outlives the BE-0207 retry (a sub-second blip smoother), so it is kept distinct from both a
    transient blip and a decoded test outcome: it names an honest "the runner crashed" failure, so a
    lost two-finger gesture never masquerades as an assertion mismatch (`actual='idle'`). `delivered`
    records whether the failed call had reached the runner, so the crash-recovery layer can tell a
    safe-to-re-issue read from a write that must not be re-applied.
    """

    def __init__(self, message: str, *, method: str = "", delivered: bool = False) -> None:
        super().__init__(message)
        self.method = method
        self.delivered = delivered


@dataclass(frozen=True)
class _Reply:
    """A decoded runner response.

    `elements` carries the `GET /elements` payload (each item is the normalized element fields plus
    its `handle`); `png` carries raw `GET /screenshot` bytes.
    """

    status: str
    elements: list[dict[str, Any]] | None = None
    png: bytes | None = field(default=None, repr=False)


# (method, path, json body) -> decoded reply. Injectable so the channel logic is tested without a
# runner; the default talks HTTP to the runner's loopback server.
TransportFn = Callable[[str, str, Mapping[str, Any] | None], _Reply]

# Statuses the runner returns for an actuation request. `ok` succeeds; `stale` / `not-found` are test
# outcomes (the element vanished / could not be actuated); any other status is a runner/infra error.
_OK = "ok"
_STALE = "stale"  # the resolved handle no longer maps to a live element (the screen changed)
_NOT_FOUND = "not-found"  # the runner could not act on the handle (no matching live element)

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
# contended host (the observed `POST /gesture failed: timed out` flake), while a genuinely wedged
# runner still fails loudly rather than hanging. Kept ≤ the job's per-step budget by a wide margin.
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
# transport retry above even though it starts at the same values: the two loops bound different things
# — a screen settling between `_resolve_handle` and `_actuate` versus a transport blip inside
# `_with_retry` — so a later re-tune of one (e.g. loosening the transport budget for a slower CI
# runner) must not silently move the other. The re-query round-trip is the condition wait, not a fixed
# sleep; this backoff only spaces the attempts so the loop stays a few seconds, not sub-second.
_STALE_MAX_ATTEMPTS = 3
# exponential per retry: 0.5s, 1.0s, … between re-resolve attempts
_STALE_BACKOFF_BASE_SECONDS = 0.5

# How long a mid-run crash-recovery (BE-0287) waits for a crashed runner to come back before failing
# loudly. A different concern from the transient retry above: the observed flake left the runner gone
# for ~30s, far beyond the retry's ~1.5s budget, so this is generous enough to ride that out yet still
# bounded — a runner that is truly gone fails the run rather than hanging it.
_RECOVERY_TIMEOUT_SECONDS = 60


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
    return _Reply(status=str(status), elements=elements)


class _TransportFailure(Exception):
    """A transport-level failure from one channel attempt, tagged with whether the request reached the runner.

    Internal to the retry seam (BE-0207): `_with_retry` reads `delivered` to decide whether re-issuing
    the call could double-apply a side-effecting write. It never escapes the module — an exhausted or
    retry-ineligible failure is turned into the caller-facing `XcuitestChannelError`.
    """

    def __init__(self, message: str, *, delivered: bool) -> None:
        super().__init__(message)
        self.delivered = delivered


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


def _await_health(
    transport: TransportFn,
    *,
    timeout: float,
    poll: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Poll `GET /health` until the runner answers `ready`, returning whether it did within *timeout*.

    A bounded condition wait (no fixed sleep that ignores the condition): `True` the moment the runner
    is ready, `False` if the deadline passes first. A channel failure while the runner is down is
    swallowed and re-polled, so "not accepting connections yet" reads as not-ready, not as an error.
    Shared by `await_ready` (startup) and the crash-recovery layer (mid-run), which differ only in the
    transport and timeout they poll with.
    """
    deadline = clock() + timeout
    while True:
        try:
            if transport("GET", "/health", None).status == "ready":
                return True
        except (XcuitestChannelError, _TransportFailure):
            pass  # runner not accepting connections yet; keep probing until the deadline
        if clock() >= deadline:
            return False
        sleep(poll)


def _with_crash_recovery(
    inner: TransportFn,
    *,
    health: Callable[[float], bool],
    recovery_timeout: float = _RECOVERY_TIMEOUT_SECONDS,
) -> TransportFn:
    """Wrap *inner* so a mid-run runner crash surfaces deterministically, not as a lost gesture (BE-0287).

    The BE-0207 retry seam (*inner*) absorbs a sub-second blip; a crash outlives its budget and raises
    `XcuitestRunnerCrashError`. This layer catches that and decides by the same `delivered` split the
    seam already draws. An idempotent read — or a write that never reached the runner — waits for the
    runner to come back (via *health*, the bounded `/health` poll) and re-issues, because re-reading is
    safe. A write that may already have been delivered is never re-sent (double-actuation risk) and
    fails with a distinct crash diagnostic, so the run stops on an honest "the runner died mid-gesture"
    rather than a misleading `actual='idle'`. Every crash — recovered or not — is logged as visibly as
    the retry seam logs a retried blip (BE-0287 Unit 4), so a crashed-and-recovered run is never
    indistinguishable from one that never crashed.

    `/health` itself passes straight through: it is the probe recovery leans on, so wrapping it would
    recurse (and block a startup `await_ready` for the whole recovery window on a runner not yet up).
    """
    logger = logging.getLogger("bajutsu.xcuitest.channel")

    def transport(method: str, path: str, body: Mapping[str, Any] | None) -> _Reply:
        if path == "/health":
            return inner(method, path, body)
        try:
            return inner(method, path, body)
        except XcuitestRunnerCrashError as crash:
            logger.warning(
                "runner channel %s %s: the runner became unreachable past the retry budget — a mid-run crash: %s",
                method,
                path,
                crash,
            )
            if not _is_retry_eligible(method, delivered=crash.delivered):
                raise XcuitestRunnerCrashError(
                    f"runner channel {method} {path} failed after delivery: the runner did not confirm "
                    "the write, which may have been lost and cannot be safely re-applied (mid-run crash)",
                    method=method,
                    delivered=crash.delivered,
                ) from crash
            if not health(recovery_timeout):
                raise XcuitestRunnerCrashError(
                    f"runner channel {method} {path} failed: the runner crashed mid-run and did not "
                    f"recover within {recovery_timeout}s",
                    method=method,
                    delivered=crash.delivered,
                ) from crash
            logger.warning(
                "runner channel %s %s: the runner recovered from a mid-run crash; re-issuing the idempotent call",
                method,
                path,
            )
            return inner(method, path, body)

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
            raise _TransportFailure(str(exc), delivered=delivered) from exc
        finally:
            conn.close()

    return transport


def _http_transport(host: str, port: int) -> TransportFn:
    """The real transport: a crash-resilient, bounded-retry channel to the runner's loopback server.

    Two layers over the raw socket: BE-0207's `_with_retry` smooths a sub-second blip, and BE-0287's
    `_with_crash_recovery` rides out a mid-run crash (idempotent re-issue) or fails loudly on a write it
    must not re-send. The crash-recovery health poll uses the single-attempt raw transport so probing
    stays fast, not the retried one.
    """
    raw = _raw_http_transport(host, port)
    return _with_crash_recovery(
        _with_retry(raw),
        health=lambda timeout: _await_health(raw, timeout=timeout),
    )


class XcuitestDriver:
    """Driver for the iOS Simulator via a resident XCUITest runner (semantic, identifier-based)."""

    name = "xcuitest"

    # Beyond idb: a semantic tap (by handle, no coordinates), native condition waiting, and the
    # two-finger gestures idb raises UnsupportedAction for. No NETWORK — network evidence comes from
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport if transport is not None else _http_transport(host, port)
        # Injectable so the stale re-resolution backoff (BE-0289) adds no wall time under test.
        self._sleep = sleep

    # --- the channel ---

    def _query_with_handles(self) -> tuple[list[base.Element], dict[int, str]]:
        """A snapshot plus a map from each element's object identity to its per-snapshot handle.

        Keyed by `id()` of the returned dicts: `resolve_unique` returns one of these very objects, so
        the resolved element's handle is an O(1) identity lookup — the element is acted on by the
        exact handle the runner minted for it, never re-resolved on the runner side.
        """
        reply = self._transport("GET", "/elements", None)
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

    def _resolve_handle(self, sel: base.Selector) -> str:
        """Resolve *sel* to a unique element Python-side and return its snapshot handle.

        Raises `AmbiguousSelector` / `ElementNotFound` before any actuation request is sent.
        """
        elements, handles = self._query_with_handles()
        el = base.resolve_unique(elements, sel)
        return handles[id(el)]

    def _actuate(self, path: str, body: Mapping[str, Any], sel: base.Selector) -> None:
        # A `stale` reply means the screen re-snapshotted between resolve and actuate, so the handle
        # no longer maps to a live element. The runner returns `stale` *before* touching anything
        # (Router.swift), so re-issuing cannot double-actuate — re-query and re-actuate while the same
        # selector still resolves uniquely (BE-0289). Zero/many matches raise ElementNotFound /
        # AmbiguousSelector out of `_resolve_handle` and fail immediately, spending no further attempts.
        request: dict[str, Any] = dict(body)
        for attempt in range(1, _STALE_MAX_ATTEMPTS + 1):
            reply = self._transport("POST", path, request)
            if reply.status == _OK:
                return
            if reply.status == _STALE:
                if attempt == _STALE_MAX_ATTEMPTS:
                    raise base.ElementNotFound(f"element vanished (stale handle): {sel!r}")
                self._sleep(_STALE_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
                request["handle"] = self._resolve_handle(sel)
                continue
            if reply.status == _NOT_FOUND:
                raise base.ElementNotFound(f"no actuatable element for: {sel!r}")
            # Any other status (e.g. an "error" from a 500 / malformed response) is a runner failure,
            # not a test outcome — fail loudly rather than masking it as element-not-found.
            raise XcuitestChannelError(
                f"runner error actuating {path} (status={reply.status}): {sel!r}"
            )

    # --- Driver Protocol ---

    def query(self) -> list[base.Element]:
        elements, _ = self._query_with_handles()
        return elements

    def tap(self, sel: base.Selector) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel)}, sel)

    def double_tap(self, sel: base.Selector) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel), "taps": 2}, sel)

    def long_press(self, sel: base.Selector, duration: float) -> None:
        self._actuate("/tap", {"handle": self._resolve_handle(sel), "duration": duration}, sel)

    def tap_point(self, p: base.Point) -> None:
        # A raw coordinate tap (system alerts and the like), the one path with no element/handle.
        reply = self._transport("POST", "/tap", {"point": [p[0], p[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"coordinate tap failed ({reply.status}) at {p}")

    def pinch(self, sel: base.Selector, scale: float) -> None:
        handle = self._resolve_handle(sel)
        self._actuate("/gesture", {"handle": handle, "kind": "pinch", "scale": scale}, sel)

    def rotate(self, sel: base.Selector, radians: float) -> None:
        handle = self._resolve_handle(sel)
        self._actuate("/gesture", {"handle": handle, "kind": "rotate", "radians": radians}, sel)

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        reply = self._transport("POST", "/swipe", {"from": [frm[0], frm[1]], "to": [to[0], to[1]]})
        if reply.status != _OK:
            raise XcuitestChannelError(f"swipe failed ({reply.status})")

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        # A real XCUITest drag scrolls iOS scroll views, so a directional scroll is just a swipe.
        self.swipe(frm, to)

    def select_option(self, sel: base.Selector, option: str) -> None:
        raise base.UnsupportedAction(
            "selectOption は <select> を持つ web バックエンド専用; iOS ネイティブに <select> はない"
        )

    def back(self) -> None:
        # iOS has no hardware back: tap the OS navigation back button, the same element idb taps, so
        # `back` behaves identically across the iOS backends. Reuses `tap` rather than re-issuing the
        # actuate call, mirroring idb's `back` (BE-0210).
        self.tap({"id": base.OS_BACK_BUTTON})

    def type_text(self, text: str) -> None:
        reply = self._transport("POST", "/type", {"text": text})
        if reply.status != _OK:
            raise XcuitestChannelError(f"type failed ({reply.status})")

    def delete_text(self, count: int) -> None:
        # A run of backspaces on the focused field (BE-0265); XCUIElement types the delete key natively.
        reply = self._transport("POST", "/deleteText", {"count": count})
        if reply.status != _OK:
            raise XcuitestChannelError(f"deleteText failed ({reply.status})")

    def select_all(self) -> None:
        reply = self._transport("POST", "/selectAll", {})
        if reply.status != _OK:
            raise XcuitestChannelError(f"selectAll failed ({reply.status})")

    def copy_selection(self) -> None:
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
        if not _await_health(self._transport, timeout=timeout, poll=poll):
            raise XcuitestChannelError(
                f"xcuitest runner did not come up within {timeout}s (health never ready)"
            )
