"""Install, start, and read from the resident server, and narrow its hierarchy dumps."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import http.client
import math
import select
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from bajutsu.common.drivers.adb import (
    ActOutcome,
    ActRequest,
    AdbActUncertain,
    AdbActUnsupported,
    AdbPickerRefused,
    AdbResidentError,
    AdbResidentTimeout,
    HierarchyRead,
    slice_hierarchy_root,
)

from ._process import _Process
from ._shared import _SERVER_APK, _TEST_APK, logger
from .budgets import ACTION_TIMEOUT_S, PICKER_TIMEOUT_S, READ_TIMEOUT_S, WAIT_BUDGET_MS

if TYPE_CHECKING:
    from .keepalive import Keepalive

# The response header the resident server stamps `GET /source` with: the device-clock time
# (`SystemClock.uptimeMillis`) of the most recent accessibility event it had observed (BE-0332 Unit 3).
# Carried in a header so the XML body stays byte-identical to `uiautomator dump`'s, keeping
# `parse_hierarchy` and `narrowed_root` unchanged.
_READ_MARK_HEADER = "X-Bajutsu-Read-Mark"

# The response header carrying each opted-in view's own `View.getZ()` (BE-0355 Unit 3), in the same
# header rather than the body and for the same reason as the mark above. Its value is
# `<key>=<z>` pairs joined by `;`, where the key names the node by what the host can recompute from
# the `<node>` it is reading — see `adb.py`'s `_native_z_key`. Absent on a server that does not
# report it and on an app that opted no view in.
_NATIVE_Z_HEADER = "X-Bajutsu-Native-Z"

# The response header `POST /act` stamps when an accessibility event postdated the injection it just
# made (BE-0339 Unit 5), carrying that event's device-clock time. Absent when the device saw none
# within its budget — and absent from an older server that never waited at all — so its presence, and
# nothing else, is what tells the driver the tree has already caught up with the gesture.
_ACT_PUBLISH_HEADER = "X-Bajutsu-Act-Publish"

# The status the resident server answers when the identity the host sent no longer names the same
# number of nodes on its own dump: the screen moved between the two resolves, so nothing was injected.
_STALE_STATUS = 409

# What an older resident server answers for a path it does not serve. Permanent for the lease, unlike a
# socket fault, so the driver latches it instead of probing again on every gesture.
_NO_ENDPOINT_STATUS = 404

# What `/setPickerValue` answers when it will not, or cannot, land the wheel (BE-0480). The body leads
# with one of `_PICKER_REFUSALS` ahead of a colon, kept in step with the server's reason tokens.
_REFUSED_STATUS = 422
_PICKER_REFUSALS = frozenset(
    {"not-a-picker-wheel", "picker-value-absent", "picker-walk-exhausted", "picker-immovable"}
)

# SystemUI owns the status and navigation bars — separate windows that `dumpWindowHierarchy` traverses
# but the platform `uiautomator dump` (active window only) omits. Dropping them is a uniform
# system-chrome filter, not per-app config, so the resident dump yields the same Elements as the dump
# path (prime directive 3, app-agnostic).
_SYSTEM_DECOR_PACKAGES = frozenset({"com.android.systemui"})


# Which one-shot warnings this process has already given. A garbled reply repeats on every gesture,
# so the first is the diagnosis and the rest are noise. Per process rather than per lease — unlike
# `AdbDriver`'s own `_mark_warned` / `_clock_warned` / `_act_warned`, which are per driver — because
# what this reports is a property of the device server's build, not of one lease: a server that
# garbles a reply garbles it for every lease it serves, and repeating the diagnosis per lease of a
# long `serve` session would say nothing new.
_warned: set[str] = set()


def narrowed_root(xml: str) -> tuple[ET.Element | None, bool]:
    """Parse `xml` once and drop its system-decor windows; the tree, and whether any were dropped.

    `dumpWindowHierarchy` emits one top-level `<node>` per window; `uiautomator dump` scopes to the
    active window. Removing the SystemUI status/navigation-bar windows reconciles the two so
    `parse_hierarchy` produces identical Elements. A tree with no system window (the active-window dump
    itself) comes back untouched with False, and unparseable input yields `(None, False)` so the
    driver's existing empty-tree handling still applies.

    The tree, not a string: the parse this had to do to strip a window is the same parse the driver
    would otherwise repeat on the string it produced, so handing the tree over is what removes the
    host's second pass over every read (BE-0407 unit 23).

    Scope: this drops only SystemUI decor. The Android e2e lane (BE-0208) exercises the resident path
    across the showcase scenarios, including one that raises the IME (`search`); the `permission`
    scenario does not exercise this — Android pre-grants the permission
    (`demos/showcase/scenarios/permission.yaml`), so no dialog ever appears there. A permission-dialog
    window leaking past this filter is therefore not yet caught by CI; broadening the filter for that
    case is still a design decision deferred rather than guessed at here.
    """
    root = slice_hierarchy_root(xml)
    if root is None:
        return None, False
    decor = [window for window in root if window.get("package") in _SYSTEM_DECOR_PACKAGES]
    for window in decor:
        root.remove(window)
    return root, bool(decor)


def _is_stale(conn: http.client.HTTPConnection) -> bool:
    """Whether `conn` is unfit to carry another request — the peer closed it, or bytes are waiting.

    A zero-timeout `select` answers that *before* any byte of the next request is sent, so the
    ordinary idle close reconnects cleanly instead of surfacing as a half-sent request whose
    delivery is ambiguous — the distinction `act` cannot afford to guess through, since a re-sent
    gesture is a second touch.
    """
    sock = conn.sock
    if sock is None:
        return True
    try:
        readable, _, _ = select.select([sock], [], [], 0)
        # Readable at all, not just readable-and-closed. On a correctly drained idle connection
        # there is nothing to read, so bytes waiting *before* a request goes out mean the two ends
        # disagree about where the last reply ended — and reusing the socket would let the next
        # `getresponse()` parse those bytes as this request's answer.
        return bool(readable)
    except (OSError, ValueError):
        # `ValueError` is the already-closed socket: its descriptor is -1, which `select` refuses
        # outright rather than reporting as unreadable. Either way the answer is the same one this
        # guard exists to give — reconnect, because writing onto a socket whose state cannot be
        # established is exactly the ambiguity a gesture must not be sent through.
        return True


@contextlib.contextmanager
def _channel(
    host_port: int, keepalive: Keepalive | None, timeout: float
) -> Iterator[http.client.HTTPConnection]:
    """The connection one call should use, kept or one-shot, retired correctly either way.

    A `keepalive` is retired on any exception and kept on a clean return — including a return down a
    branch that read a non-200 and chose not to raise, which is a complete exchange leaving the
    connection perfectly good (`fetch_clock`'s non-200 and `act`'s `409`). Without one, the
    connection is closed on the way out exactly as it was before this unit.

    Getting the connection can itself fail, and that failure is converted here rather than left to
    each caller. Before the connection was kept, `HTTPConnection` connected lazily inside
    `request()`, so a refused connect surfaced inside the caller's own `try` and became an
    `AdbResidentError`; connecting eagerly moved it outside. `act` is the caller that noticed — a
    raw `OSError` escaped it, escaped `_device_act`'s three-exception catch, and failed the step
    on the one fault class that is provably safe to fall back from, since nothing had been sent.
    """
    if keepalive is None:
        conn = http.client.HTTPConnection("127.0.0.1", host_port, timeout=timeout)
        try:
            yield conn
        finally:
            conn.close()
        return
    try:
        conn = keepalive.connection(host_port, timeout)
    except TimeoutError as exc:
        keepalive.discard()
        raise AdbResidentTimeout(f"resident channel timed out on port {host_port}: {exc}") from exc
    except (OSError, http.client.HTTPException) as exc:
        keepalive.discard()
        raise AdbResidentError(f"resident channel unreachable on port {host_port}: {exc}") from exc
    try:
        yield conn
    except BaseException:
        keepalive.discard()
        raise
    keepalive.succeeded()


def fetch_source(
    host_port: int,
    since: float | None = None,
    *,
    timeout: float = READ_TIMEOUT_S,
    native_z: bool = False,
    keepalive: Keepalive | None = None,
) -> HierarchyRead:
    """GET the resident server's current hierarchy over the forwarded loopback host port.

    Returns the XML plus its read mark (BE-0332 Unit 3): the `X-Bajutsu-Read-Mark` header carries the
    device-clock time of the most recent accessibility event the reader had seen, so the driver can
    trust a read only once it postdates the gesture. A response without the header (an older server)
    yields a None mark, and the driver's barrier falls back to its wall-clock budget — never a failure.

    Args:
        since: The device-clock mark the read must postdate (BE-0332 Unit 4). When given, it rides on
            the request as `?since=`, and the resident server blocks until an accessibility event
            postdates it before dumping once — collapsing the host's re-poll into one round trip. None
            (a read with no gesture pending) requests the current hierarchy with no wait.
        native_z: Ask the device to measure each opted-in view's own `View.getZ()` (BE-0355). Off by
            default because the walk that answers it covers every node on every read and returns
            nothing at all for an app that opted no view in (BE-0407 unit 18); a target turns it on
            in its own config.
        keepalive: The lease's reused connection, when there is one.

    Raises:
        AdbResidentTimeout: no answer inside `timeout`; the server may still be working on the request.
        AdbResidentError: the channel could not be reached or did not answer 200 — an infrastructure
            failure the driver catches to fall back to `uiautomator dump`, never a test outcome.
    """
    query: dict[str, float] = {"budgetMs": WAIT_BUDGET_MS}
    if since is not None:
        query["since"] = since
    if native_z:
        query["nativeZ"] = 1
    path = f"/source?{urllib.parse.urlencode(query)}"
    try:
        with _channel(host_port, keepalive, timeout) as conn:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                raise AdbResidentError(f"resident server returned HTTP {resp.status}")
            # An absent or malformed mark here degrades the driver's barrier to its wall-clock budget
            # rather than failing the read: the mark only ever tightens the wait.
            mark = _parse_mark(resp.getheader(_READ_MARK_HEADER))
            measured_z = _parse_native_z(resp.getheader(_NATIVE_Z_HEADER))
            # A truncated/garbled body (a mid-write device server) must degrade to the dump fallback,
            # not escape past the driver's AdbResidentError-only catch — whether it surfaces as a
            # UnicodeDecodeError (garbled bytes) or an http.client.HTTPException (IncompleteRead from
            # a short body, BadStatusLine/UnknownProtocol from a malformed status line).
            return HierarchyRead(body.decode("utf-8"), mark, native_z=measured_z)
    except TimeoutError as exc:
        raise AdbResidentTimeout(f"resident channel timed out on port {host_port}: {exc}") from exc
    except (OSError, UnicodeDecodeError, http.client.HTTPException) as exc:
        raise AdbResidentError(f"resident channel unreachable on port {host_port}: {exc}") from exc


def act(
    host_port: int,
    request: ActRequest,
    *,
    timeout: float = ACTION_TIMEOUT_S,
    keepalive: Keepalive | None = None,
    want_tree: bool = True,
) -> ActOutcome:
    """Ask the resident server to perform one gesture on an element the host already resolved.

    The element crosses as its four accessibility fields plus its ordinal among the nodes sharing them,
    never as a coordinate: the device re-finds it in a dump of its own and reads the bounds microseconds
    before injecting, closing the window in which a settling screen moves out from under a coordinate
    the host computed a round trip earlier.

    Returns:
        The device's answer: `acted` False for a `409` — the identity no longer names the same nodes
        there, so the host must re-resolve rather than let a coordinate be guessed — and, when the
        gesture landed, whether an accessibility event postdated it before the server replied
        (`_ACT_PUBLISH_HEADER`). A server that never sends that header reports no confirmation, which
        is what leaves the driver's read-lag barrier armed exactly as it was. A confirmed gesture also
        carries the tree the device dumped after that publish (`read`), which is the host's next read
        already answered (BE-0407 unit 19) — unless `want_tree` is False, which tells the device not
        to take that dump at all.

    Raises:
        AdbResidentError: the channel could not be reached, or answered anything else — including the
            `404` an older server without the endpoint returns. The driver degrades to its coordinate
            actuators, so a device that cannot serve this is never worse off than before.
    """
    fields = {
        "budgetMs": str(WAIT_BUDGET_MS),
        "tree": "1" if want_tree else "0",
        "kind": request.kind,
        "index": str(request.index),
        "count": str(request.count),
        "rid": request.identity[0],
        "desc": request.identity[1],
        "text": request.identity[2],
        "cls": request.identity[3],
    }
    if request.duration_ms is not None:
        fields["durationMs"] = str(request.duration_ms)
        # The server holds the press before it can answer, so the hold is not part of its budget.
        timeout += request.duration_ms / 1000
    if request.since is not None:
        fields["since"] = str(request.since)
    # A longer timeout than a read: the server honors the `since` mark and settles before it injects,
    # and a press-and-hold then holds for its own duration on top of that.
    with _channel(host_port, keepalive, timeout) as conn:
        try:
            conn.request("POST", "/act?" + urllib.parse.urlencode(fields))
        except (OSError, http.client.HTTPException) as exc:
            # Nothing left the host, so nothing was injected: the caller may safely take the
            # coordinate path. This is the only fault where that is safe.
            raise AdbResidentError(
                f"resident actuation unreachable on port {host_port}: {exc}"
            ) from exc
        try:
            resp = conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            # The request went out, and the device injects before it answers, so the gesture may
            # already have happened. Re-actuating on the coordinate path would be a second touch.
            raise AdbActUncertain(
                f"resident actuation was sent but its reply was lost on port {host_port}: {exc}"
            ) from exc
        body = raw.decode("utf-8", "replace").strip()
        if resp.status == _STALE_STATUS:
            logger.debug("resident actuation reported the target moved: %s", body)
            return ActOutcome(acted=False, published_mark=None)
        if resp.status == _NO_ENDPOINT_STATUS:
            raise AdbActUnsupported(f"resident server has no /act endpoint (HTTP {resp.status})")
        if resp.status != 200:
            raise AdbResidentError(f"resident actuation returned HTTP {resp.status}: {body}")
        # `_parse_mark` reads a malformed value as no value, so a garbled header degrades to "the
        # device could not confirm" — the barrier stays armed — rather than failing a gesture that
        # actually landed.
        return ActOutcome(
            acted=True,
            published_mark=_parse_mark(resp.getheader(_ACT_PUBLISH_HEADER)),
            read=_act_read(resp, raw),
        )


def set_picker_value(
    host_port: int,
    request: ActRequest,
    value: str,
    *,
    timeout: float = PICKER_TIMEOUT_S,
    keepalive: Keepalive | None = None,
    want_tree: bool = True,
) -> ActOutcome:
    """Ask the resident server to walk the wheel the host resolved to the row showing `value` (BE-0480).

    The wheel crosses the way `act`'s target does — its identity, ordinal, and count, never a
    coordinate — and the device steps it one row at a time through accessibility scroll actions,
    reading the row back after each step.

    Returns:
        `acted` False for the `409` stale reply, so the host re-resolves; otherwise the landed
        wheel's publish confirmation and caught-up tree, exactly as `act` reports a landed gesture.

    Raises:
        AdbPickerRefused: the device answered with one of the endpoint's refusals (not a wheel, no
            such row, walk budget spent), or failed mid-walk.
        AdbActUnsupported: the server predates `/setPickerValue`.
        AdbActUncertain: the request went out but its reply was lost, so the wheel may have moved.
        AdbResidentError: the channel could not be reached, so nothing was sent.
    """
    fields = {
        "budgetMs": str(WAIT_BUDGET_MS),
        "tree": "1" if want_tree else "0",
        "index": str(request.index),
        "count": str(request.count),
        "rid": request.identity[0],
        "desc": request.identity[1],
        "text": request.identity[2],
        "cls": request.identity[3],
        "value": value,
    }
    if request.since is not None:
        fields["since"] = str(request.since)
    with _channel(host_port, keepalive, timeout) as conn:
        try:
            conn.request("POST", "/setPickerValue?" + urllib.parse.urlencode(fields))
        except (OSError, http.client.HTTPException) as exc:
            raise AdbResidentError(
                f"resident picker request unreachable on port {host_port}: {exc}"
            ) from exc
        try:
            resp = conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise AdbActUncertain(
                f"resident picker request was sent but its reply was lost on port {host_port}: {exc}"
            ) from exc
        if resp.status == 200:
            return ActOutcome(
                acted=True,
                published_mark=_parse_mark(resp.getheader(_ACT_PUBLISH_HEADER)),
                read=_act_read(resp, raw),
            )
    body = raw.decode("utf-8", "replace").strip()
    if resp.status == _STALE_STATUS:
        logger.debug("resident picker request reported the wheel moved: %s", body)
        return ActOutcome(acted=False, published_mark=None)
    if resp.status == _NO_ENDPOINT_STATUS:
        raise AdbActUnsupported(
            f"resident server has no /setPickerValue endpoint (HTTP {resp.status})"
        )
    reason, sep, detail = body.partition(":")
    if resp.status == _REFUSED_STATUS and sep and reason in _PICKER_REFUSALS:
        raise AdbPickerRefused(reason, detail.strip())
    raise AdbPickerRefused("failed", f"HTTP {resp.status}: {body}")


def type_text(
    host_port: int,
    text: str,
    since: float | None = None,
    *,
    timeout: float = ACTION_TIMEOUT_S,
    keepalive: Keepalive | None = None,
) -> None:
    """Ask the resident server to enter `text` into the field that holds input focus.

    The text crosses in the request body, base64-encoded, because a query string is what a proxy or a
    log would keep and a typed value may be a secret. Encoding also leaves the server's character
    reader counting bytes one-for-one against `Content-Length`. `since` is the device-clock mark the
    server's read of the field must postdate, because it builds the new value from that read.

    Raises:
        AdbActUnsupported: the server predates `/type`, so a rebuilt server is the only remedy.
        AdbActUncertain: the request went out but its reply was lost, so the text may already be in
            the field and sending it again could enter it twice.
        AdbResidentError: the channel could not be reached, or the device refused — no focused field,
            a password field, a field without `ACTION_SET_TEXT`. Its message is the device's reason,
            which never quotes the text.
    """
    body = base64.b64encode(text.encode("utf-8"))
    query: dict[str, float] = {"budgetMs": WAIT_BUDGET_MS}
    if since is not None:
        query["since"] = since
    path = "/type?" + urllib.parse.urlencode(query)
    with _channel(host_port, keepalive, timeout) as conn:
        try:
            conn.request("POST", path, body=body)
        except (OSError, http.client.HTTPException) as exc:
            raise AdbResidentError(
                f"resident text entry unreachable on port {host_port}: {exc}"
            ) from exc
        try:
            resp = conn.getresponse()
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise AdbActUncertain(
                f"resident text entry was sent but its reply was lost on port {host_port}: {exc}"
            ) from exc
    if resp.status == _NO_ENDPOINT_STATUS:
        raise AdbActUnsupported(f"resident server has no /type endpoint (HTTP {resp.status})")
    if resp.status != 200:
        reason = raw.decode("utf-8", "replace").strip()
        raise AdbResidentError(f"resident text entry returned HTTP {resp.status}: {reason}")


def _act_read(resp: http.client.HTTPResponse, raw: bytes) -> HierarchyRead | None:
    """The caught-up tree a confirmed gesture's reply carries, or None when it carries none.

    The read mark is what distinguishes the two bodies this endpoint can answer: a bare `ok` from an
    unconfirmed gesture, and the post-publish dump from a confirmed one (BE-0407 unit 19). Keyed off
    the header rather than the content type because that is the same signal the driver already trusts
    to decide the tree is not stale — a body with no mark is one the driver could not have used
    anyway. An older server sends neither, so it needs no version negotiation.

    A body that will not decode or will not narrow yields None rather than raising: the gesture itself
    landed, and losing this optimisation only costs the read it would have saved. It is still said out
    loud once per process, because it is the same mid-write-server symptom `fetch_source` tears the
    channel down over — at debug only, a server garbling every reply would present as an unexplained
    slowdown and nothing else.
    """
    mark = _parse_mark(resp.getheader(_READ_MARK_HEADER))
    if mark is None:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _warn_once("resident actuation returned an undecodable tree; reading again instead")
        return None
    root, narrowed = narrowed_root(text)
    if root is None:
        _warn_once("resident actuation returned an unparseable tree; reading again instead")
        return None
    return HierarchyRead(text, mark, root=root, narrowed=narrowed)


def _warn_once(message: str) -> None:
    if message not in _warned:
        _warned.add(message)
        logger.warning(message)


def _parse_mark(raw: str | None) -> float | None:
    """A device-clock mark header as a float, or None when the header is absent or unparseable.

    Shared by the two headers that carry one: `X-Bajutsu-Read-Mark` on a read and
    `X-Bajutsu-Act-Publish` on an actuation. What an absent mark *costs* differs between them, so each
    caller states its own consequence; what they share is that a mark is never required — an older
    server sends neither header, and reading None rather than raising is what keeps such a server
    working.
    """
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_native_z(raw: str | None) -> dict[str, float]:
    """The `X-Bajutsu-Native-Z` header as content key to measured position, empty when absent.

    A malformed pair is dropped rather than failing the read: `nativeZ` is diagnostic (BE-0355), so a
    garbled reading costs one element its position and leaves the rest of the tree intact — the same
    honest absence an app that never opted in reports.
    """
    if not raw:
        return {}
    found: dict[str, float] = {}
    for pair in raw.split(";"):
        key, sep, value = pair.rpartition("=")
        if not sep or not key:
            continue
        try:
            z = float(value)
        except ValueError:
            continue
        # `NaN` / `Infinity` parse but name no position, the same reading `native_z_from_json`
        # already refuses for a value read back off an artifact.
        if math.isfinite(z):
            found[key] = z
    return found


def fetch_clock(
    host_port: int, *, timeout: float = READ_TIMEOUT_S, keepalive: Keepalive | None = None
) -> float | None:
    """GET the resident server's current device clock (`SystemClock.uptimeMillis`), or None.

    Read just before a gesture (BE-0332 Unit 3) so a later read must postdate it, on the device's own
    clock, to count as caught up. Returns None on any fault or a non-200 rather than raising: the mark
    is an optimisation over the wall-clock budget, so a clock hiccup slows the barrier at worst, never
    fails a read or accepts a stale one.
    """
    try:
        with _channel(host_port, keepalive, timeout) as conn:
            conn.request("GET", "/clock")
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                return None
            return float(body.decode("utf-8").strip())
    # `AdbResidentError` included since `_channel` converts a failed connect into one: this probe is
    # documented never to raise, and `_capture_mark` relies on that — a clock hiccup must slow the
    # barrier, never fail the gesture that was about to take a mark.
    except (OSError, ValueError, UnicodeDecodeError, http.client.HTTPException, AdbResidentError):
        return None


def _apk_digest(path: Path) -> str:
    """A content digest of one APK, naming the exact bytes a device would be given.

    Read in chunks rather than whole: an instrumentation APK runs to a megabyte, and nothing here
    needs it in memory.
    """
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def server_apks_built(server_apk: Path = _SERVER_APK, test_apk: Path = _TEST_APK) -> bool:
    """True when both resident-server APKs exist, so the resident read channel can start.

    The default-on gate reads this to pick the resident channel over `uiautomator dump` only when
    `make -C BajutsuAndroidUIAutomatorServer build` has produced both outputs; a fresh clone that never built
    them (the build outputs are gitignored) falls back to the dump path untouched.
    """
    return server_apk.exists() and test_apk.exists()


def _default_spawn(argv: list[str]) -> _Process:
    # The instrumentation blocks (serve() never returns), so it runs in the background for the lease;
    # its output is drained to DEVNULL so a full pipe never wedges it.
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _parse_forward_port(stdout: str) -> int:
    """The host port `adb forward tcp:0 …` chose, printed on stdout.

    Read as the last bare-number line rather than the whole output, because adb prepends its own
    chatter whenever the invocation happens to be the one that starts the server ("* daemon not
    running; starting now at tcp:5037", "* daemon started successfully"). Parsing the whole string
    would raise on exactly those runs, and a failed forward takes the resident channel down with it —
    the lease then reads through `uiautomator dump`, which carries no device mark, so every read-lag
    barrier silently falls back to spending its full budget. Tolerating the banner keeps a cosmetic
    line from costing the channel.
    """
    ports = [line.strip() for line in stdout.splitlines() if line.strip().isdigit()]
    if not ports:
        raise AdbResidentError(f"adb forward did not report a host port: {stdout!r}")
    return int(ports[-1])
