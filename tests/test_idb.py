"""Tests for the idb backend: describe-all parsing, commands, coordinate tap."""

from __future__ import annotations

import subprocess

import pytest

from bajutsu import simctl
from bajutsu.drivers import base
from bajutsu.drivers.idb import (
    IdbDriver,
    _validated_udid,
    connect_cmd,
    disconnect_cmd,
    parse_describe_all,
    swipe_cmd,
    tap_cmd,
)

FIXTURE = """
[
  {"AXUniqueId":"settings.open","AXLabel":"設定","type":"Button","enabled":true,
   "frame":{"x":0,"y":0,"width":100,"height":40}},
  {"AXUniqueId":"submit","AXLabel":"送信","type":"Button","enabled":false,
   "frame":{"x":0,"y":50,"width":100,"height":40}},
  {"AXLabel":"static","type":"StaticText","frame":{"x":0,"y":100,"width":100,"height":20}}
]
"""

NDJSON = (
    '{"AXUniqueId":"a","AXLabel":"A","type":"Button","frame":{"x":1,"y":2,"width":3,"height":4}}\n'
    '{"AXUniqueId":"b","AXLabel":"B","type":"Cell","frame":{"x":0,"y":0,"width":1,"height":1}}\n'
)


def _fake_client_manager(client: object) -> type:
    """A fake fb-idb `ClientManager` whose `from_udid` yields `client` (shared by companion tests).

    Only the connection/manager plumbing is faked here — each test supplies its own `client` with
    whatever `text` / `send_events` / `key_sequence` behavior it needs.
    """

    class _FakeConn:
        async def __aenter__(self) -> object:
            return client

        async def __aexit__(self, *exc: object) -> None:
            return None

    class _FakeManager:
        def __init__(self, **_kw: object) -> None:
            pass

        def from_udid(self, udid: str) -> _FakeConn:
            return _FakeConn()

    return _FakeManager


def test_parse_describe_all() -> None:
    els = parse_describe_all(FIXTURE)
    assert len(els) == 3
    assert els[0]["identifier"] == "settings.open"
    assert els[0]["label"] == "設定"
    assert els[0]["traits"] == ["button"]
    assert els[0]["frame"] == (0.0, 0.0, 100.0, 40.0)
    assert base.Trait.NOT_ENABLED in els[1]["traits"]  # enabled: false
    assert els[2]["identifier"] is None  # static text, no AXUniqueId


def test_parse_ndjson_fallback() -> None:
    els = parse_describe_all(NDJSON)
    assert [e["identifier"] for e in els] == ["a", "b"]


def test_tap_resolves_frame_center() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            return FIXTURE
        calls.append(args)
        return ""

    driver = IdbDriver("U", run=run)
    driver.tap({"id": "settings.open"})
    # center of (0,0,100,40) -> (50, 20)
    assert calls == [tap_cmd("U", 50, 20)]
    # A short dwell is always included so the tap actuates a UISwitch (see idb.py).
    assert calls[0] == ["idb", "ui", "tap", "--udid", "U", "50", "20", "--duration", "0.1"]


def test_scroll_delegates_to_a_real_swipe_drag() -> None:
    # A directional scroll on iOS is a real `idb ui swipe` drag, so scroll delegates to swipe
    # (BE-0227) — an OS-level drag already scrolls scroll views.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            return FIXTURE
        calls.append(args)
        return ""

    IdbDriver("U", run=run).scroll((10, 20), (30, 40))
    assert calls == [swipe_cmd("U", 10, 20, 30, 40)]


def test_capabilities_has_no_semantic_tap() -> None:
    assert base.Capability.SEMANTIC_TAP not in IdbDriver("U", run=lambda a: "[]").capabilities()


def test_capabilities_advertises_every_permission_but_notifications() -> None:
    # simctl privacy has no TCC service for iOS notification authorization (BE-0276).
    caps = IdbDriver("U", run=lambda a: "[]").capabilities()
    assert base.permission_capability("camera") in caps
    assert base.permission_capability("location") in caps
    assert base.permission_capability("notifications") not in caps


def test_select_option_unsupported() -> None:
    # <select> is a web control with no iOS-native counterpart, so the backend refuses (BE-0191).
    driver = IdbDriver("U", run=lambda a: "[]")
    with pytest.raises(base.UnsupportedAction):
        driver.select_option({"id": "nav.theme-picker"}, "midnight")


# A near-empty tree as idb returns mid-transition: one element, no identifier.
EMPTY = '[{"AXLabel":"","frame":{"x":0,"y":0,"width":0,"height":0}}]'


def _scripted(responses: list[str]) -> tuple[object, list[int]]:
    """A run() that returns describe-all responses in order (one per describe-all call),
    holding the last once exhausted. Returns (run, calls) where calls[0] counts
    describe-all invocations."""
    seq = list(responses)
    calls = [0]

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            calls[0] += 1
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    return run, calls


def test_query_retries_through_transient_empty() -> None:
    # Full tree first (establishes a richer baseline), then a transient empty, then full.
    run, calls = _scripted([FIXTURE, EMPTY, FIXTURE])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0  # no real sleeping in the test

    assert len(driver.query()) == 3  # baseline: _max_seen becomes 3
    els = driver.query()  # hits EMPTY then recovers to the full tree
    assert len(els) == 3
    assert els[0]["identifier"] == "settings.open"
    assert calls[0] == 3  # 1 baseline + (1 empty + 1 recovered)


def test_query_does_not_retry_genuinely_sparse_screen() -> None:
    # No richer tree has ever been seen, so a small tree is taken at face value.
    run, calls = _scripted([EMPTY])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0

    assert len(driver.query()) == 1
    assert calls[0] == 1  # returned immediately, no retry


def test_query_returns_after_bounded_retries_when_empty_persists() -> None:
    # After a rich tree, a persistent empty tree is retried a bounded number of
    # times and then returned — query() must not hang masking a real empty screen.
    run, calls = _scripted([FIXTURE, EMPTY])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0

    assert len(driver.query()) == 3  # baseline
    calls[0] = 0
    assert len(driver.query()) == 1  # gives up and returns the empty tree
    assert calls[0] == 1 + IdbDriver._EMPTY_RETRIES  # initial + bounded retries


def test_empty_backoff_grows_exponentially_then_caps() -> None:
    # The transient-empty retry backs off exponentially (recovering fast when the empty
    # clears quickly, spacing out later) and caps so the total stays within the prior bound.
    driver = IdbDriver("U", run=lambda a: "[]")
    seq = [driver._empty_backoff(i) for i in range(IdbDriver._EMPTY_RETRIES)]
    assert seq == [0.05, 0.1, 0.2, 0.2, 0.2]  # base 0.05, doubling, capped at 0.2
    assert sum(seq) <= 1.0  # no further than the previous fixed-0.2 * 5 bound


# --- BE-0231 Unit 6: recover a wedged idb accessibility bridge ---
#
# On a cold Simulator's first app launch, idb's accessibility bridge sometimes fails to attach to
# the app window: describe-all returns only the top-level `application` element with a zero-area
# frame, even though the app has fully rendered (its screenshot shows the content). The signature is
# a lone application-root element with no geometry — distinct from idb's generic mid-transition
# empty (no type, caught by the transient-empty retry), so it can be told apart even on the first
# screen, where a genuinely sparse screen still carries real geometry.

# The wedge: only the application root, and it has no measured bounds.
WEDGED = '[{"type":"Application","frame":{"x":0,"y":0,"width":0,"height":0}}]'

# A genuinely sparse but rendered screen: one element, but with real geometry — not a wedge.
SPARSE_REAL = (
    '[{"AXUniqueId":"only","type":"StaticText","frame":{"x":0,"y":0,"width":100,"height":20}}]'
)


def _recording(describe: list[str]) -> tuple[object, list[list[str]]]:
    """A run() that returns describe-all responses in order (holding the last once exhausted) and
    records every argv it is handed, so a test can count describe-all / disconnect / connect."""
    seq = list(describe)
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        if "describe-all" in args:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    return run, calls


def _counts(calls: list[list[str]]) -> tuple[int, int, int]:
    """(describe-all, disconnect, connect) invocation counts from a recorded call list."""
    describe = sum("describe-all" in a for a in calls)
    disconnect = calls.count(disconnect_cmd("U"))
    connect = calls.count(connect_cmd("U"))
    return describe, disconnect, connect


def test_is_ax_bridge_wedged_keys_on_a_lone_zero_frame_application() -> None:
    # The exact wedge signature: one application-root element with no geometry.
    assert IdbDriver._is_ax_bridge_wedged(parse_describe_all(WEDGED)) is True
    # A rendered application root (real frame) is not a wedge.
    real_app = '[{"type":"Application","frame":{"x":0,"y":0,"width":393,"height":852}}]'
    assert IdbDriver._is_ax_bridge_wedged(parse_describe_all(real_app)) is False
    # idb's generic mid-transition empty (no type → no application trait) is not a wedge:
    # the transient-empty retry owns that case, not the companion reset.
    assert IdbDriver._is_ax_bridge_wedged(parse_describe_all(EMPTY)) is False
    # A populated screen is never a wedge.
    assert IdbDriver._is_ax_bridge_wedged(parse_describe_all(FIXTURE)) is False


def test_query_resets_companion_on_ax_bridge_wedge() -> None:
    # First screen (no richer tree seen): the bridge is wedged, so query() resets the companion
    # connection and re-reads, recovering the real tree the app had already rendered.
    run, calls = _recording([WEDGED, FIXTURE])
    driver = IdbDriver("U", run=run)

    els = driver.query()
    assert len(els) == 3  # recovered the full tree after the reset
    describe, disconnect, connect = _counts(calls)
    assert (disconnect, connect) == (1, 1)  # per-udid reset, not a global `idb kill`
    assert describe == 2  # initial wedged read + one post-reset read


def test_query_does_not_reset_a_genuinely_sparse_screen() -> None:
    # A rendered screen with one real-framed element is genuinely sparse, not a wedge:
    # no companion reset, returned at face value.
    run, calls = _recording([SPARSE_REAL])
    driver = IdbDriver("U", run=run)

    assert len(driver.query()) == 1
    describe, disconnect, connect = _counts(calls)
    assert (disconnect, connect) == (0, 0)
    assert describe == 1  # returned immediately, no reset, no re-read


def test_query_returns_after_bounded_resets_when_wedge_persists() -> None:
    # A companion that never recovers must not hang query(): it resets a bounded number of times,
    # then returns the degenerate tree so the wait fails loudly with the Unit 1 diagnostic.
    run, calls = _recording([WEDGED])
    driver = IdbDriver("U", run=run)

    assert len(driver.query()) == 1  # gave up and returned the wedged tree
    describe, disconnect, connect = _counts(calls)
    assert (disconnect, connect) == (IdbDriver._AX_RESET_RETRIES, IdbDriver._AX_RESET_RETRIES)
    assert describe == 1 + IdbDriver._AX_RESET_RETRIES  # initial read + one per reset


def test_query_yields_a_recurring_wedge_without_burning_transient_empty_backoff() -> None:
    # A wedge that recurs *after* a rich tree was already seen (so _max_seen >= _READY_MIN) also
    # matches _is_transient_empty (len < _READY_MIN and a richer tree was seen). Without the wedge
    # guard, _read_settled_tree would burn its full _EMPTY_RETRIES exponential-backoff loop on the
    # wedge — a same-companion re-read can never clear it — before query() finally resets. It must
    # instead hand the wedge straight back to query() so the companion reset (which *can* clear it)
    # fires promptly, keeping the describe-all count well below the compounding worst case.
    run, calls = _recording([FIXTURE, WEDGED, FIXTURE])
    driver = IdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0  # no real sleeping in the test

    assert len(driver.query()) == 3  # baseline: _max_seen becomes 3
    calls.clear()
    els = driver.query()  # hits the wedge, resets the companion, recovers the full tree
    assert len(els) == 3

    describe, disconnect, connect = _counts(calls)
    assert (disconnect, connect) == (1, 1)  # the companion reset fired
    # Wedge yielded promptly: initial wedged read + one post-reset read. No transient-empty backoff
    # burned on it (the compounding worst case would be ~1 + _EMPTY_RETRIES describe-all calls
    # *per* reset attempt before the reset ever fires).
    assert describe == 2
    assert describe < 1 + IdbDriver._EMPTY_RETRIES  # well below the compounding worst case


def test_reset_companion_tolerates_a_dropped_connection() -> None:
    # disconnect/connect are best-effort: a disconnect with no live connection (or a connect race)
    # raises CalledProcessError that must not mask the wedge — the re-query decides recovery.
    def run(args: list[str]) -> str:
        raise subprocess.CalledProcessError(1, args)

    IdbDriver("U", run=run)._reset_companion()  # must not raise


def test_wait_for_is_single_shot() -> None:
    # BE-0118: wait_for checks the current screen once; the deadline poll lives in wait_until.
    present = IdbDriver("U", run=lambda a: FIXTURE)
    assert present.wait_for({"id": "settings.open"}) is True
    absent = IdbDriver("U", run=lambda a: "[]")
    assert absent.wait_for({"id": "nope"}) is False


def test_wait_until_polls_idb_until_the_element_appears() -> None:
    # Absent on the first reads, then it shows up: wait_until must keep polling idb's
    # single-shot wait_for, not give up after one check.
    run, calls = _scripted([EMPTY, EMPTY, FIXTURE])
    driver = IdbDriver("U", run=run)
    assert base.wait_until(driver, {"id": "settings.open"}, timeout=5, poll=0) is True
    assert calls[0] >= 3  # polled past the empty trees until the element appeared


# --- _stable_key projection ---


def test_stable_key_ignores_volatile_fields() -> None:
    # Two trees that differ only in volatile fields (value, traits, label)
    # should produce the same stable key.
    tree_a: list[base.Element] = [
        {
            "identifier": "btn",
            "label": "Save",
            "value": "0",
            "traits": ["button"],
            "frame": (10.0, 20.0, 100.0, 40.0),
        },
    ]
    tree_b: list[base.Element] = [
        {
            "identifier": "btn",
            "label": "Done",
            "value": "1",
            "traits": ["button", "notEnabled"],
            "frame": (10.0, 20.0, 100.0, 40.0),
        },
    ]
    assert IdbDriver._stable_key(tree_a) == IdbDriver._stable_key(tree_b)

    # Different frames → different key.
    tree_c: list[base.Element] = [
        {
            "identifier": "btn",
            "label": "Save",
            "value": "0",
            "traits": ["button"],
            "frame": (10.0, 25.0, 100.0, 40.0),
        },
    ]
    assert IdbDriver._stable_key(tree_a) != IdbDriver._stable_key(tree_c)


def test_query_updates_stable_key_cache() -> None:
    driver = IdbDriver("U", run=lambda a: FIXTURE)
    assert driver._last_stable_key is None
    driver.query()
    assert driver._last_stable_key is not None
    assert driver._last_stable_key == IdbDriver._stable_key(parse_describe_all(FIXTURE))


# A tree with different frames (mid-animation) from FIXTURE.
ANIMATING = """
[
  {"AXUniqueId":"settings.open","AXLabel":"設定","type":"Button","enabled":true,
   "frame":{"x":0,"y":5,"width":100,"height":40}},
  {"AXUniqueId":"submit","AXLabel":"送信","type":"Button","enabled":false,
   "frame":{"x":0,"y":55,"width":100,"height":40}},
  {"AXLabel":"static","type":"StaticText","frame":{"x":0,"y":105,"width":100,"height":20}}
]
"""

# Same frames as FIXTURE but different volatile fields (value, label, traits).
VOLATILE_CHANGED = """
[
  {"AXUniqueId":"settings.open","AXLabel":"Settings","type":"Button","enabled":true,
   "AXValue":"new","frame":{"x":0,"y":0,"width":100,"height":40}},
  {"AXUniqueId":"submit","AXLabel":"Submit","type":"Button","enabled":true,
   "frame":{"x":0,"y":50,"width":100,"height":40}},
  {"AXLabel":"other","type":"StaticText","frame":{"x":0,"y":100,"width":100,"height":20}}
]
"""


# --- _settle ---


def test_settle_skips_on_first_call() -> None:
    # No cached key yet; _settle returns after a single query without polling.
    run, calls = _scripted([FIXTURE])
    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    tree = driver._settle()
    assert len(tree) == 3
    assert calls[0] == 1  # only one describe-all call


def test_settle_returns_immediately_when_stable() -> None:
    # Cache matches the current tree; _settle returns in one query.
    run, calls = _scripted([FIXTURE])
    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    driver.query()  # populates the cache
    calls[0] = 0
    tree = driver._settle()
    assert len(tree) == 3
    assert calls[0] == 1  # one query, cache hit, no polling


def test_settle_polls_until_frames_stabilize() -> None:
    # Cache has the old tree, current tree is animating, then stabilizes.
    # Sequence: query() for cache → _settle reads ANIMATING (mismatch) →
    #   polls: ANIMATING again (different from FIXTURE but same as prev) → stable.
    run, calls = _scripted([FIXTURE, ANIMATING, ANIMATING])
    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    driver.query()  # cache = FIXTURE key
    calls[0] = 0
    tree = driver._settle()
    assert len(tree) == 3
    # 1 (initial read: ANIMATING, mismatches cache) + 1 (poll: ANIMATING again, matches prev)
    assert calls[0] == 2


# --- type_text: value goes over the gRPC companion client, never onto argv (BE-0155) ---


def test_type_text_sends_value_over_companion_not_argv(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A secret/OTP typed into a field must not land on the idb process's argv, where a
    # co-tenant could read it via ps/proc. The driver sends it over the fb-idb gRPC client
    # (idb_companion), so no subprocess carries it and no argv is built for it.
    typed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        IdbDriver, "_type_text", staticmethod(lambda udid, text: typed.append((udid, text)))
    )
    ran: list[list[str]] = []
    IdbDriver("U", run=lambda a: ran.append(a) or "").type_text("${secrets.password}")

    assert typed == [("U", "${secrets.password}")]  # value reaches the companion path
    assert ran == []  # no subprocess/argv was built for the value


def test_type_text_fails_fast_when_companion_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A missing idb_companion must raise a clear, actionable error here, not an opaque one
    # deep inside fb-idb from a None companion_path.
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="idb_companion not found"):
        IdbDriver("U").type_text("hi")


def test_delete_text_sends_backspaces_over_companion_not_argv(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Backspaces travel the same gRPC companion path type_text uses (BE-0265), so nothing lands
    # on the idb argv — mirrors the type_text seam.
    deleted: list[tuple[str, int]] = []
    monkeypatch.setattr(
        IdbDriver, "_delete_text", staticmethod(lambda udid, count: deleted.append((udid, count)))
    )
    ran: list[list[str]] = []
    IdbDriver("U", run=lambda a: ran.append(a) or "").delete_text(3)

    assert deleted == [("U", 3)]
    assert ran == []  # no subprocess/argv was built


def test_delete_text_via_companion_sends_hid_backspaces_not_text_control_char(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    # BE-0280 regression: delete must send Delete/Backspace HID key events (keycode 42) via
    # `client.key_sequence`, not the `\b` control character through `client.text()`. fb-idb's text
    # keymap has no entry for `\b` and raises "No keycode found for" — so the old `text("\b")` path
    # crashed mid-flow on a real device. Drives the real `_delete_text_via_companion` against a fake
    # gRPC companion client (the only external dependency the mock rule allows), pinning that it
    # never touches `text` and issues exactly `count` backspace keycodes.
    import shutil

    management = pytest.importorskip("idb.grpc.management")  # skip on the gate (no idb extra)

    from bajutsu.drivers import idb as idb_mod

    class _FakeClient:
        def __init__(self) -> None:
            self.text_calls: list[str] = []
            self.key_sequences: list[list[int]] = []

        async def text(self, text: str) -> None:
            self.text_calls.append(text)

        async def key_sequence(self, key_sequence: list[int]) -> None:
            self.key_sequences.append(list(key_sequence))

    client = _FakeClient()

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/idb_companion")
    monkeypatch.setattr(management, "ClientManager", _fake_client_manager(client))

    idb_mod._delete_text_via_companion("U", 3)
    assert client.key_sequences == [[idb_mod._HID_KEY_DELETE] * 3]  # three backspace HID keys
    assert client.text_calls == []  # never routes a control char through the text keymap

    idb_mod._type_text_via_companion("U", "hi")
    assert client.text_calls == ["hi"]  # typing still goes through the text path


def test_type_text_falls_back_to_paste_for_unmappable_characters(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    # fb-idb's HID keymap only covers the US keyboard layout, so typing a Japanese (or other
    # non-Latin) character makes the real `client.text()` raise a bare `Exception("No keycode found
    # for ...")`, always before any key is sent (`text_to_events` builds the whole event list up
    # front). The driver must recover by pasting the whole string — a hardware Cmd+V chord over the
    # same HID channel reaches UIKit's Paste for the focused field (verified on-device) — rather than
    # crashing the run (prime directive 2). The pasteboard is left holding `text`, not restored to
    # whatever it held before: restoring immediately would race the app actually reading it for the
    # paste, and getting that race wrong would silently deliver stale text instead of `text`.
    import shutil

    management = pytest.importorskip("idb.grpc.management")  # skip on the gate (no idb extra)

    from idb.common.types import HIDDirection, HIDKey, HIDPress

    from bajutsu.drivers import idb as idb_mod

    class _FakeClient:
        def __init__(self) -> None:
            self.text_calls: list[str] = []
            self.sent_events: list[list[object]] = []

        async def text(self, text: str) -> None:
            self.text_calls.append(text)
            raise Exception(f"No keycode found for {text[0]}")  # mirrors fb-idb's own raise

        async def send_events(self, events: list[object]) -> None:
            self.sent_events.append(list(events))

    client = _FakeClient()

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/idb_companion")
    monkeypatch.setattr(management, "ClientManager", _fake_client_manager(client))

    set_calls: list[str] = []
    monkeypatch.setattr(simctl.Env, "set_clipboard", lambda self, text: set_calls.append(text))

    idb_mod._type_text_via_companion("U", "で")

    assert client.text_calls == ["で"]  # the direct HID path is tried first
    assert set_calls == ["で"]  # seeded with the value; never restored (see docstring for why)
    assert client.sent_events == [
        [
            HIDPress(action=HIDKey(keycode=idb_mod._HID_KEY_LEFT_GUI), direction=HIDDirection.DOWN),
            HIDPress(action=HIDKey(keycode=idb_mod._HID_KEY_V), direction=HIDDirection.DOWN),
            HIDPress(action=HIDKey(keycode=idb_mod._HID_KEY_V), direction=HIDDirection.UP),
            HIDPress(action=HIDKey(keycode=idb_mod._HID_KEY_LEFT_GUI), direction=HIDDirection.UP),
        ]
    ]


def test_type_text_reraises_an_unrelated_companion_exception(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    # The paste fallback must trigger only on the exact "No keycode found for" shape fb-idb raises
    # for an unmappable character — matching on `startswith` guards against masking an unrelated
    # companion/connection failure as "needs paste" (prime directive 2: fail loudly, don't paper
    # over a genuine error with the wrong recovery). A differently worded exception from
    # `client.text()` must propagate unchanged, and the paste path must never run.
    import shutil

    management = pytest.importorskip("idb.grpc.management")  # skip on the gate (no idb extra)

    from bajutsu.drivers import idb as idb_mod

    class _FakeClient:
        def __init__(self) -> None:
            self.sent_events: list[list[object]] = []

        async def text(self, text: str) -> None:
            raise Exception("boom")

        async def send_events(self, events: list[object]) -> None:
            self.sent_events.append(list(events))  # pragma: no cover — must never be reached

    client = _FakeClient()

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/idb_companion")
    monkeypatch.setattr(management, "ClientManager", _fake_client_manager(client))

    set_calls: list[str] = []
    monkeypatch.setattr(simctl.Env, "set_clipboard", lambda self, text: set_calls.append(text))

    with pytest.raises(Exception, match="boom"):
        idb_mod._type_text_via_companion("U", "hi")

    assert set_calls == []  # the paste fallback never ran
    assert client.sent_events == []


def test_select_and_copy_are_unsupported_and_route_to_xcuitest() -> None:
    # idb is coordinate-only, so select-all / copy have no actuation; they fail loudly and point at
    # codegen→XCUITest, mirroring how multi-touch gestures are refused (BE-0265). The refusal is
    # honest: idb does not advertise TEXT_SELECTION, so preflight rejects a `select`/`copy` scenario
    # before any device work rather than letting it fail late (BE-0280).
    driver = IdbDriver("U", run=lambda a: "[]")
    assert base.Capability.TEXT_SELECTION not in driver.capabilities()
    with pytest.raises(base.UnsupportedAction, match="XCUITest"):
        driver.select_all()
    with pytest.raises(base.UnsupportedAction, match="XCUITest"):
        driver.copy_selection()


class _Clock:
    """A fake monotonic clock: `sleep` advances it, so a wall-clock deadline is deterministic."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _moving_run(counter: list[int]) -> object:
    """A run() whose every describe-all returns a fresh y offset, so the frame key never repeats."""

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            counter[0] += 1
            y = float(counter[0])
            return (
                f'[{{"AXUniqueId":"a","AXLabel":"A","type":"Button",'
                f'"frame":{{"x":0,"y":{y},"width":100,"height":40}}}}]'
            )
        return ""

    return run


def _two_element_tree(y: float) -> str:
    # A two-element tree (>= _READY_MIN) so a moving frame is never mistaken for a transient-empty
    # read and retried by the shared backoff — only element "a" moves; "b" is a static anchor.
    return (
        f'[{{"AXUniqueId":"a","AXLabel":"A","type":"Button",'
        f'"frame":{{"x":0,"y":{y},"width":100,"height":40}}}},'
        f'{{"AXUniqueId":"b","AXLabel":"B","type":"Button",'
        f'"frame":{{"x":0,"y":500,"width":100,"height":40}}}}]'
    )


def test_settle_keeps_polling_past_the_old_count_until_frames_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0299 Unit 4: the settle window is bounded by wall-clock, not a fixed read count — so a frame
    # that moves for MORE reads than the old 3-poll cap, then rests, still settles on the resting
    # frame. The old count-bound would have returned a still-moving frame and tapped a stale point.
    clock = _Clock()
    monkeypatch.setattr("bajutsu.drivers.idb.time", clock)
    # Cache a resting frame, then move for 5 reads (past the old cap of 3) before two equal reads.
    seq = [_two_element_tree(y) for y in (100, 110, 130, 150, 165, 170, 170)]
    run, _ = _scripted(seq)
    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0.05
    driver._SETTLE_DEADLINE_S = 2.0

    driver.query()  # cache the resting (y=100) key
    tree = driver._settle()
    # Settled on the resting frame (y=170), not any moving frame — proves it polled past the old cap.
    assert tree[0]["frame"] == (0.0, 170.0, 100.0, 40.0)
    assert clock.t < driver._SETTLE_DEADLINE_S  # returned on stability, before the deadline


def test_settle_gives_up_at_the_wall_clock_deadline_when_never_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A screen that never stops moving must not spin forever: the poll is bounded by a wall-clock
    # deadline (independent of read cost), after which _settle returns the latest tree (BE-0299).
    clock = _Clock()
    monkeypatch.setattr("bajutsu.drivers.idb.time", clock)
    counter = [0]
    driver = IdbDriver("U", run=_moving_run(counter))  # type: ignore[arg-type]
    driver._SETTLE_POLL_S = 0.1
    driver._SETTLE_DEADLINE_S = 0.5

    driver.query()  # cache the first frame
    before = counter[0]
    driver._settle()
    assert clock.t >= driver._SETTLE_DEADLINE_S  # bounded by the deadline, then it stops
    assert 3 < (counter[0] - before) <= 8  # more than the old 3-poll cap, but bounded by wall-clock


def test_settle_defaults_bound_by_wall_clock_not_read_count() -> None:
    # BE-0299 Unit 4: the class pins a wall-clock deadline and a small non-zero poll interval, the
    # same shape AdbDriver adopted in BE-0245 — no fixed poll count remains.
    assert IdbDriver._SETTLE_DEADLINE_S > 0
    assert IdbDriver._SETTLE_POLL_S > 0
    assert not hasattr(IdbDriver, "_SETTLE_MAX_POLLS")


def test_settle_ignores_volatile_field_changes() -> None:
    # Cache has FIXTURE; current tree has same frames but different labels/values/traits.
    # _settle should treat this as stable (projection matches).
    run, calls = _scripted([FIXTURE, VOLATILE_CHANGED])
    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    driver.query()  # cache = FIXTURE key
    calls[0] = 0
    tree = driver._settle()
    assert len(tree) == 3
    assert calls[0] == 1  # one query, projection matches cache, no polling


def test_tap_settles_before_resolving() -> None:
    # Settle must wait for frames to stop moving before resolving tap coordinates.
    calls: list[list[str]] = []
    seq_list = [FIXTURE, ANIMATING, ANIMATING]
    describe_calls = [0]

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            describe_calls[0] += 1
            return seq_list.pop(0) if len(seq_list) > 1 else seq_list[0]
        calls.append(args)
        return ""

    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    driver.query()  # populate cache with FIXTURE
    describe_before = describe_calls[0]
    calls.clear()
    driver.tap({"id": "settings.open"})

    # settle: 1 (ANIMATING, mismatch) + 1 (ANIMATING, stable) = 2 describe-all
    # resolve: 0 (uses settled tree) → total = 2
    assert describe_calls[0] - describe_before == 2
    # Tap used the ANIMATING tree's frame center: (0,5,100,40) → (50, 25)
    assert calls == [tap_cmd("U", 50, 25)]


def test_resolve_uses_initial_tree_without_extra_query() -> None:
    # When initial_tree is provided and the element is present, _resolve skips query().
    run, calls = _scripted([FIXTURE])
    driver = IdbDriver("U", run=run)

    tree = parse_describe_all(FIXTURE)
    el = driver._resolve({"id": "settings.open"}, initial_tree=tree)
    assert el["identifier"] == "settings.open"
    assert calls[0] == 0  # no describe-all call; used initial_tree


def test_stable_key_handles_none_identifiers() -> None:
    # None identifiers should not crash the sort (None vs str raises TypeError).
    tree: list[base.Element] = [
        {
            "identifier": None,
            "label": "X",
            "value": None,
            "traits": [],
            "frame": (0.0, 0.0, 1.0, 1.0),
        },
        {
            "identifier": "a",
            "label": "A",
            "value": None,
            "traits": [],
            "frame": (0.0, 0.0, 2.0, 2.0),
        },
    ]
    key = IdbDriver._stable_key(tree)
    assert len(key) == 2
    # None → "" for sort safety; first element in sorted order is "" < "a".
    assert key[0][0] == ""
    assert key[1][0] == "a"


def test_validated_udid_accepts_real_udids() -> None:
    # `booted` (idb's current-device alias) and UUID- / device-shaped ids pass unchanged.
    for good in ["booted", "U", "A1B2C3D4-1122-3344-5566-77889900AABB", "emulator-5554"]:
        assert _validated_udid(good) == good


def test_validated_udid_rejects_injection() -> None:
    # A udid from --udid / config that could inject an idb option (leading `-`) or reach a
    # subprocess argv with a shell metacharacter / space is rejected before it is used. Raises
    # simctl.DeviceError (not a bare ValueError) so the CLI's device-fault handler catches it.
    for bad in ["-rf", "--udid", "a b", "a;b", "a$b", "a`b", "", "x" * 129]:
        with pytest.raises(simctl.DeviceError, match="invalid udid"):
            _validated_udid(bad)


def test_driver_rejects_bad_udid_at_construction() -> None:
    # IdbDriver validates the udid in __init__, so a bad id fails at the object boundary and every
    # use of self.udid — argv builders and the gRPC companion path alike — is covered.
    with pytest.raises(simctl.DeviceError, match="invalid udid"):
        IdbDriver("bad;rm")
    assert IdbDriver("booted", run=lambda a: "[]").udid == "booted"
