"""Prove the resident server's settle re-fetches the tree, not a pinned cache (BE-0490).

Android caches accessibility nodes per connection and evicts them only on an accessibility event. A
change that publishes no evicting event therefore leaves the cached node in place, so before
BE-0490 two consecutive dumps could agree because both serialized the same pinned cache, and the
resident settle certified the pre-change tree as current. The server now drops that cache between
settle dumps on API 34 and above. This module pins both halves of that promise against the real
device:

* a screen change that arrives with no evicting event appears in the next settled read — the case
  fails against a server without the drop whenever its own precondition holds;
* a static target still settles in two dumps, so the drop costs a healthy read nothing.

The freshness case does not take the fixture's silence on trust. It asserts, from the server's per-
window evicting-event mark (`X-Bajutsu-Evict-Mark`), that no evicting event named the app's own
window while the screen changed; one that did makes the fixture invalid and fails the case rather
than letting it pass on an eviction the drop never had to cover. It also writes the event types the
change published (`X-Bajutsu-Event-Log`) under `runs/`, which the fault-injection lane uploads with
its run.

The fixture is the Compose conformance screen's withheld-event pair (`WithheldEventPair`, seeded by
the `conformance.withheld` sentinel): a tap on the target changes the count a separate value view
reports as content-desc, and the only event published is the target's click, which refreshes the
target alone. A Compose element cannot serve as the value: on the emulator lane a Compose mirrored
value published a window-content change of its own, which the precondition rightly rejects.
Everything here speaks the resident server's HTTP directly over its own `adb forward`, below the
driver: the driver's own read-lag barrier and re-polls would otherwise ride over a stale read and
hide exactly what this module measures. The driver is used only to launch the app and the server.

Runs in the Android fault-injection lane (`make -C demos/showcase/android e2e-fault-injection`),
never in `make check`: the `ondevice` marker is deselected by the gate's default, and a module-
level skip drops it whenever `BAJUTSU_FAULT_INJECTION_SERIAL` is unset. Run serially (`-n0`), like
that lane.
"""

from __future__ import annotations

import http.client
import os
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import ondevice_evidence
import pytest

from bajutsu.common.backend_cli import adb
from bajutsu.common.backend_cli.adb_resident import _parse_forward_port, server_apks_built
from bajutsu.common.backend_cli.adb_resident.budgets import (
    ACTION_TIMEOUT_S,
    READ_TIMEOUT_S,
    WAIT_BUDGET_MS,
)
from bajutsu.common.config import Effective, load_config, require_android, resolve
from bajutsu.common.evidence import intervals
from bajutsu.common.runner.launch import launch_driver

pytestmark = pytest.mark.ondevice

_serial = os.environ.get("BAJUTSU_FAULT_INJECTION_SERIAL")
if not _serial:
    pytest.skip(
        "the accessibility-cache drop check needs BAJUTSU_FAULT_INJECTION_SERIAL (a booted API 34+ "
        "emulator with the showcase Compose a11y APK and the resident server built) — it runs in the "
        "Android E2E workflow, never the fast gate",
        allow_module_level=True,
    )
SERIAL: str = adb.resolve_serial(_serial)

_CONFIG_PATH = Path("demos/showcase/showcase.config.yaml")
_TARGET = "showcase-compose"

# The floor `UiAutomation.clearCache()` sets (BE-0490 §2): below it the server keeps the pre-item
# settle, so there is nothing for this module to prove.
_API_FLOOR = 34

# The withheld-event pair (ConformanceScreen.kt's `WithheldEventPair`): a tap on the target changes
# the count the value view reports as content-desc, and no event names the value view.
_TAP_TARGET = "conformance.withheld.target"
_VALUE = "conformance.withheld.value"
# A static node on the same screen, for the two-dump case.
_STATIC = "conformance.ready"
# Boot straight into the withheld-event screen; the leading `,` is the conformance wire format's own
# (the conformance lane's `_wire` explains it).
_CONFORMANCE_ENV = {"SHOWCASE_UITEST": "1", "SHOWCASE_CONFORMANCE": ",conformance.withheld"}

# Kept in step with ResidentServerTest.kt's `EVICT_MARK_HEADER` / `EVENT_LOG_HEADER` and with the
# `READ_MARK_HEADER` the host driver already reads.
_EVICT_MARK_HEADER = "X-Bajutsu-Evict-Mark"
_EVENT_LOG_HEADER = "X-Bajutsu-Event-Log"
# The server's `SO_TIMEOUT_MS`: how long it holds an idle connection before serving the next one.
_SERVER_IDLE_OUT_S = 5.0
# Event types whose platform eviction clears every window's nodes, whichever package published them
# (`AccessibilityCache.onAccessibilityEvent`), so one from any window can re-fetch the value view.
_GLOBAL_EVICTIONS = frozenset({"TYPE_WINDOW_STATE_CHANGED", "TYPE_WINDOWS_CHANGED"})
# The server's `EVENT_LOG_MAX`, and what it writes for an event with no package name.
_EVENT_LOG_MAX = 64
_NO_PACKAGE = "-"
# The one event a tap on the target is expected to publish; it refreshes only its source node.
_CLICK = "TYPE_VIEW_CLICKED"
# The server's logcat tag, and the line `WaitBudget.report` writes for an `/act` request's settle.
_SERVER_TAG = "BajutsuResidentServer"
_ACT_WAITS = "act waits: "

_EVENT_LOG_OUT = Path("runs/diagnostics/cache-drop-event-types.txt")


@dataclass(frozen=True)
class _Reply:
    status: int
    body: str
    headers: dict[str, str]


@dataclass(frozen=True)
class _Eviction:
    window: int
    package: str
    time: int


def _parse_evictions(raw: str) -> list[_Eviction]:
    """`<windowId>:<package>=<time>;...` → one entry per window, as the server stamps it."""
    out = []
    for entry in filter(None, raw.split(";")):
        where, _, time = entry.rpartition("=")
        window, _, package = where.partition(":")
        out.append(_Eviction(int(window), package, int(time)))
    return out


@dataclass(frozen=True)
class _LogEntry:
    raw: str
    time: int
    type: str
    window: int
    package: str

    @classmethod
    def parse(cls, raw: str) -> _LogEntry:
        """`<time>:<type>:<windowId>:<package>`, as the server stamps each event."""
        time, type_, window, package = raw.split(":", 3)
        return cls(raw, int(time), type_, int(window), package)


@pytest.fixture(scope="module")
def _eff() -> Effective:
    eff = resolve(load_config(_CONFIG_PATH.read_text()), _TARGET)
    return eff.rebased(_CONFIG_PATH.resolve().parent, confine=False)


@pytest.fixture(scope="module")
def app_package(_eff: Effective) -> str:
    return require_android(_eff).package


@pytest.fixture(scope="module")
def resident_port(_eff: Effective) -> Iterator[int]:
    """A host port forwarded to the resident server the launched driver keeps alive.

    The server serves one connection at a time and holds each until its peer idles out
    (`SO_TIMEOUT_MS`), and the driver's readiness read leaves its keep-alive connection open. The
    first request here therefore waits for that connection to go, under a timeout that covers the
    idle-out plus a read: a condition wait the server releases the moment it accepts, never a guess
    at whether the driver's socket has been collected. Every later request opens and closes its own
    connection, so the server is free again for the next one.
    """
    api = int(adb.real_run(adb._adb(SERIAL, "shell", "getprop", "ro.build.version.sdk")).strip())
    if api < _API_FLOOR:
        pytest.skip(f"API {api} is below the cache-drop floor (API {_API_FLOOR})")
    # Without the APKs the driver reads through `uiautomator dump`, and there is no settle to test.
    assert server_apks_built(), (
        "build the resident server: make -C BajutsuAndroidUIAutomatorServer build"
    )
    launch_driver(SERIAL, _eff, "adb", extra_env=_CONFORMANCE_ENV)
    port = _parse_forward_port(adb.real_run(adb.forward_cmd(SERIAL)))
    try:
        reply = _request(port, "GET", "/clock", _SERVER_IDLE_OUT_S + READ_TIMEOUT_S)
        assert reply.status == 200, f"GET /clock answered {reply.status}: {reply.body}"
        yield port
    finally:
        adb.real_run(adb.forward_remove_cmd(SERIAL, port))


@pytest.fixture(autouse=True)
def _evidence(request: pytest.FixtureRequest) -> Iterator[None]:
    """Video + deviceLog for this case, kept only on failure."""
    yield from ondevice_evidence.capture(
        SERIAL,
        "cache-drop-adb",
        request,
        start_video=ondevice_evidence.android_screenrecord,
        start_log=intervals.start_logcat,
    )


def _request(port: int, method: str, path: str, timeout: float) -> _Reply:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        return _Reply(resp.status, body, dict(resp.getheaders()))
    finally:
        conn.close()


def _source(port: int, since: int | None = None) -> _Reply:
    # `events=1` asks for the event records, which the server stamps only on request.
    query: dict[str, int] = {"budgetMs": WAIT_BUDGET_MS, "events": 1}
    if since is not None:
        query["since"] = since
    reply = _request(port, "GET", f"/source?{urllib.parse.urlencode(query)}", READ_TIMEOUT_S)
    assert reply.status == 200, f"GET /source answered {reply.status}: {reply.body}"
    return reply


def _clock(port: int) -> int:
    reply = _request(port, "GET", "/clock", READ_TIMEOUT_S)
    assert reply.status == 200, f"GET /clock answered {reply.status}: {reply.body}"
    return int(reply.body.strip())


def _node(xml: str, rid: str) -> ET.Element:
    """The one node whose resource-id is `rid` — the fixture's ids are unique by construction."""
    found = [n for n in ET.fromstring(xml).iter("node") if n.get("resource-id") == rid]
    assert len(found) == 1, f"expected exactly one node {rid!r}, found {len(found)}"
    return found[0]


def _tap(port: int, xml: str, rid: str, *, want_tree: bool = False) -> None:
    """Tap the node `rid` through the server's own `/act`, from the identity `xml` shows."""
    node = _node(xml, rid)
    fields = {
        "budgetMs": str(WAIT_BUDGET_MS),
        "tree": "1" if want_tree else "0",
        "kind": "tap",
        "index": "0",
        "count": "1",
        "rid": node.get("resource-id", ""),
        "desc": node.get("content-desc", ""),
        "text": node.get("text", ""),
        "cls": node.get("class", ""),
    }
    reply = _request(port, "POST", "/act?" + urllib.parse.urlencode(fields), ACTION_TIMEOUT_S)
    assert reply.status == 200, f"POST /act answered {reply.status}: {reply.body}"


def _taps(xml: str) -> str:
    """The tap count the withheld-event value view reports, as its content-desc."""
    return _node(xml, _VALUE).get("content-desc", "")


def _device_time() -> str:
    """The device's clock as `logcat -T` takes it, so a log read starts at this case.

    The epoch form (`<seconds>.<millis>`) rather than the dated one: `adb` re-joins its arguments into
    one device shell line, so a value with a space in it would arrive as two arguments.
    """
    return adb.real_run(adb._adb(SERIAL, "shell", "date", "+%s")).strip() + ".000"


def test_a_change_with_no_evicting_event_reaches_the_next_settled_read(
    resident_port: int, app_package: str
) -> None:
    before = _source(resident_port)
    count = int(_taps(before.body))
    mark = _clock(resident_port)
    _tap(resident_port, before.body, _TAP_TARGET)
    after = _source(resident_port, since=mark)

    log = after.headers.get(_EVENT_LOG_HEADER)
    evict_raw = after.headers.get(_EVICT_MARK_HEADER)
    assert log is not None and evict_raw is not None, (
        "the resident server stamped no event record on GET /source; this module needs a server "
        "carrying BE-0490 Unit 3 (rebuild it with `make -C BajutsuAndroidUIAutomatorServer build`)"
    )
    entries = [_LogEntry.parse(e) for e in filter(None, log.split(";"))]
    since_tap = [e for e in entries if e.time > mark]
    _EVENT_LOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    _EVENT_LOG_OUT.write_text("".join(f"{e.raw}\n" for e in since_tap))

    # The precondition: nothing but the drop can have re-fetched the value view. The tap's own click
    # is expected, and the platform refreshes only its source node, which is the tapped target, never
    # the value view beside it. Any other app-window event could have evicted the value, so it makes
    # the fixture invalid, and the case fails rather than passing on the platform's own eviction. The
    # bounded log must not have dropped an entry since the tap, or a dropped one could be that
    # eviction; a log short of its bound has dropped nothing. An event with no package (`-`) cannot be
    # ruled out of the app's window, so it counts as the app's. A window-level change counts from any
    # package, because the platform clears the whole cache for it, the value view's node included.
    assert len(entries) < _EVENT_LOG_MAX or entries[0].time <= mark, (
        f"invalid fixture: the full event log no longer reaches back to the tap at {mark}, so it "
        f"cannot show that no evicting event reached the app: {[e.raw for e in entries]}"
    )
    suspects = (app_package, _NO_PACKAGE)
    unexpected = [
        e.raw
        for e in since_tap
        if (e.package in suspects and e.type != _CLICK) or e.type in _GLOBAL_EVICTIONS
    ]
    # The per-window mark covers the same interval independently of the log: its newest eviction in
    # the app's window must be one the log shows as the click.
    clicks = {(e.window, e.time) for e in since_tap if e.type == _CLICK}
    unexplained = [
        ev
        for ev in _parse_evictions(evict_raw)
        if ev.package in suspects and ev.time > mark and (ev.window, ev.time) not in clicks
    ]
    assert not unexpected and not unexplained, (
        "invalid fixture: the app published a cache-evicting event besides the tap's own click, "
        f"so this read proves nothing about the drop. Events: {unexpected}; evictions the log does "
        f"not explain as the click: {unexplained}; all events since the tap: "
        f"{[e.raw for e in since_tap]}"
    )

    assert _taps(after.body) == str(count + 1), (
        f"the settled read still shows {_taps(after.body)!r} after a tap that made it "
        f"{count + 1}: the settle certified the pinned accessibility cache. Events since the tap: "
        f"{[e.raw for e in since_tap]}"
    )


def test_a_static_target_still_settles_in_two_dumps(resident_port: int) -> None:
    # Asserted on the target's own bounds (`settledActBounds`, what `/act` settles) rather than on the
    # whole dump, which carries SystemUI's clock and would turn a minute tick into a third dump.
    xml = _source(resident_port).body
    started = _device_time()
    _tap(resident_port, xml, _STATIC)
    lines = adb.real_run(
        adb._adb(SERIAL, "logcat", "-d", "-T", started, "-s", f"{_SERVER_TAG}:D")
    ).splitlines()
    waits = [line.split(_ACT_WAITS, 1)[1] for line in lines if _ACT_WAITS in line]
    assert waits, f"no `{_ACT_WAITS.strip()}` record in the server's log since {started}:\n" + (
        "\n".join(lines)
    )
    laps = waits[-1].split()
    dumps = sum(1 for lap in laps if lap.startswith("dump="))
    assert dumps == 2, f"a static target took {dumps} settle dumps (laps: {waits[-1]})"
    # Two dumps with the drop between them, not two dumps because the drop never ran.
    assert any(lap.startswith("drop=") for lap in laps), (
        f"the settle recorded no completed cache drop (laps: {waits[-1]})"
    )
