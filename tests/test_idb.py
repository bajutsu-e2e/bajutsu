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


def test_select_and_copy_are_unsupported_and_route_to_xcuitest() -> None:
    # idb is coordinate-only, so select-all / copy have no actuation; they fail loudly and point at
    # codegen→XCUITest, mirroring how multi-touch gestures are refused (BE-0265).
    driver = IdbDriver("U", run=lambda a: "[]")
    with pytest.raises(base.UnsupportedAction, match="XCUITest"):
        driver.select_all()
    with pytest.raises(base.UnsupportedAction, match="XCUITest"):
        driver.copy_selection()


def test_settle_gives_up_after_max_polls() -> None:
    # Frames change on every read; _settle gives up after the bound.
    counter = [0]

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            counter[0] += 1
            # Each call returns a tree with a unique y offset so the key never repeats.
            y = float(counter[0])
            return (
                f'[{{"AXUniqueId":"a","AXLabel":"A","type":"Button",'
                f'"frame":{{"x":0,"y":{y},"width":100,"height":40}}}}]'
            )
        return ""

    driver = IdbDriver("U", run=run)
    driver._SETTLE_POLL_S = 0

    driver.query()  # cache (counter = 1, y = 1)
    before = counter[0]
    driver._settle()
    # 1 initial (y=2, mismatches cache y=1) + _SETTLE_MAX_POLLS polls (y=3,4,5 — each differs)
    assert counter[0] - before == 1 + IdbDriver._SETTLE_MAX_POLLS


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
