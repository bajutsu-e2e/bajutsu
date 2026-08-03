"""Tests for the adb backend: uiautomator dump parsing, commands, coordinate tap, transient retry.

These cover the adb driver's selector mapping over captured `uiautomator dump` XML, frame-centre
taps, the transient-empty retry, and ambiguous-fails-fast — all over an injected `run`, no device
needed (BE-0007 Unit 7, fast gate).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from pathlib import Path

import pytest

import bajutsu.drivers.adb as adb_driver_mod
from bajutsu import adb
from bajutsu.drivers import base
from bajutsu.drivers.adb import (
    AdbActUnsupported,
    AdbDriver,
    AdbResidentError,
    HierarchyRead,
    parse_hierarchy,
)
from bajutsu.evidence import intervals

# A realistic dump: a Views native id (package-prefixed) with visible text only, a Compose testTag
# (verbatim, dotted) that mirrors its state value into content-desc à la the showcase (SPEC §2.1)
# and is also disabled, and a checked switch — exercising the whole selector mapping.
FIXTURE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" text="設定" resource-id="com.bajutsu.showcase.android.views:id/stable_refresh"
      class="android.widget.Button" content-desc="" enabled="true" checked="false"
      selected="false" bounds="[0,100][200,200]" />
    <node index="1" text="送信" resource-id="stable.submit" class="android.widget.Button"
      content-desc="sent" enabled="false" bounds="[0,200][200,300]" />
    <node index="2" text="オン" resource-id="net.toggle" class="android.widget.Switch"
      content-desc="" checked="true" bounds="[0,300][100,360]" />
  </node>
</hierarchy>
UI hierarchy dumped to: /dev/tty"""

# uiautomator's mid-transition failure: no hierarchy, just the bridge error on stdout.
NULL_ROOT = "null root node returned by UiTestAutomationBridge.\n"

# The dump is the full view hierarchy, so a structural container (the root FrameLayout) is a node
# too — one more than the three leaf elements, unlike an accessibility-only set.
FIXTURE_ELEMENT_COUNT = 4


def _by_id(els: list[base.Element], ident: str) -> base.Element:
    return next(e for e in els if e["identifier"] == ident)


def test_parse_hierarchy_selector_mapping() -> None:
    els = parse_hierarchy(FIXTURE)
    assert len(els) == FIXTURE_ELEMENT_COUNT

    # Native android:id: the `<package>:id/` prefix is stripped to the local name.
    refresh = _by_id(els, "stable_refresh")
    assert refresh["label"] == "設定"  # visible text is the label
    assert refresh["value"] is None  # no content-desc → no mirrored value
    assert refresh["traits"] == ["button"]
    assert refresh["frame"] == (0.0, 100.0, 200.0, 100.0)  # [0,100][200,200] → x,y,w,h

    # Compose testTag: dotted id verbatim; the state value is mirrored to content-desc (SPEC §2.1),
    # so `label` is the visible text and `value` reads the mirror, not the visible string.
    submit = _by_id(els, "stable.submit")
    assert submit["label"] == "送信"  # visible text is the label
    assert submit["value"] == "sent"  # content-desc mirror is the asserted value
    assert base.Trait.NOT_ENABLED in submit["traits"]  # enabled="false"

    # A checked switch: class → trait, checked → selected.
    toggle = _by_id(els, "net.toggle")
    assert toggle["traits"] == ["switch", base.Trait.SELECTED]


# The Compose tab bar (BE-0223, captured on device): a NavigationBarItem dumps as a *clickable*
# `android.view.View` with no own text — the visible label lives in a child `TextView`. So neither
# channel the shared cross-backend selector `{ label, traits: [button] }` needs is on the tappable
# node: its class ("view") is not the button trait, and its own text is empty. The driver bridges
# both — a clickable node is a button, and a clickable node without its own label derives one from
# its descendants' text — so the tab resolves the same way iOS reaches it (BE-0107).
COMPOSE_TAB = """<hierarchy rotation="0">
  <node index="0" class="android.view.View" text="" content-desc="" clickable="true"
    bounds="[440,2120][640,2340]">
    <node index="0" class="android.widget.TextView" text="Log" clickable="false"
      bounds="[510,2250][570,2300]" />
    <node index="1" class="android.view.View" text="" clickable="false"
      bounds="[500,2160][580,2230]" />
  </node>
</hierarchy>"""

# The Views tab bar renders each tab as a plain clickable `android.widget.Button` carrying its own
# text — so class ("button") already yields the trait and text already the label. The clickable
# fix must not regress this: the button trait stays present exactly once, not duplicated.
VIEWS_TAB = """<hierarchy rotation="0">
  <node index="0" class="android.widget.Button" text="Log" content-desc="" clickable="true"
    bounds="[440,2120][640,2340]" />
</hierarchy>"""


def test_compose_navigation_bar_item_derives_label_and_button_trait() -> None:
    # The tappable node is the clickable View: it gains the button trait and derives its label
    # ("Log") from the child TextView, so `{ label, traits: [button] }` resolves to it — while the
    # child TextView, having label "Log" but no button trait, does not, keeping the match unique.
    els = parse_hierarchy(COMPOSE_TAB)
    tab = next(e for e in els if base.Trait.BUTTON in e["traits"])
    assert tab["label"] == "Log"
    assert tab["traits"] == ["view", base.Trait.BUTTON]
    assert tab["frame"] == (
        440.0,
        2120.0,
        200.0,
        220.0,
    )  # the clickable node's frame, not the label's
    child = next(e for e in els if e["traits"] == ["textView"])
    assert child["label"] == "Log"
    assert base.Trait.BUTTON not in child["traits"]


def test_tap_resolves_compose_tab_by_label_and_button_trait() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return COMPOSE_TAB
        calls.append(args)
        return ""

    driver = AdbDriver("U", run=run)
    driver.tap({"label": "Log", "traits": ["button"]})
    # centre of the clickable View (440,2120,200,220) → (540, 2230), not the label TextView's centre.
    assert calls == [["adb", "-s", "U", "shell", "input", "tap", "540", "2230"]]


def test_views_tab_button_resolves_without_duplicate_button_trait() -> None:
    # A clickable android.widget.Button already carries the button trait from its class; the
    # clickable rule must not add a second one, and the own text stays the label (no derivation).
    (tab,) = parse_hierarchy(VIEWS_TAB)
    assert tab["label"] == "Log"
    assert tab["traits"] == ["button"]


def test_non_clickable_container_does_not_derive_a_label() -> None:
    # Label derivation is scoped to clickable (interactive) nodes: a plain layout container with a
    # text child stays label-less, so the tree is not flooded with synthetic container labels.
    xml = (
        '<hierarchy><node class="android.view.View" text="" clickable="false" bounds="[0,0][100,100]">'
        '<node class="android.widget.TextView" text="Inside" clickable="false" bounds="[0,0][50,50]" />'
        "</node></hierarchy>"
    )
    container = next(e for e in parse_hierarchy(xml) if e["traits"] == ["view"])
    assert container["label"] is None


def test_derived_label_skips_nested_clickable_subtree() -> None:
    # A clickable descendant is its own control (its own button + derived label), so its text is
    # not folded into the outer control's label. Here the outer tab derives "Log" (not "Log 9")
    # and the nested badge derives "9" — two distinct buttons, so `{ label: "Log", traits: [button] }`
    # still resolves the outer one uniquely rather than colliding with the badge.
    xml = """<hierarchy rotation="0">
      <node class="android.view.View" text="" clickable="true" bounds="[0,2120][200,2340]">
        <node class="android.widget.TextView" text="Log" clickable="false" bounds="[10,2250][90,2300]" />
        <node class="android.view.View" text="" clickable="true" bounds="[100,2130][190,2180]">
          <node class="android.widget.TextView" text="9" clickable="false" bounds="[110,2130][180,2180]" />
        </node>
      </node>
    </hierarchy>"""
    els = parse_hierarchy(xml)
    outer = next(e for e in els if e["frame"] == (0.0, 2120.0, 200.0, 220.0))
    assert outer["label"] == "Log"
    assert base.Trait.BUTTON in outer["traits"]
    badge = next(e for e in els if e["frame"] == (100.0, 2130.0, 90.0, 50.0))
    assert badge["label"] == "9"
    assert base.Trait.BUTTON in badge["traits"]


def test_parse_hierarchy_null_root_is_empty() -> None:
    # The transient bridge failure has no <hierarchy>, so it parses to an empty tree (retried later).
    assert parse_hierarchy(NULL_ROOT) == []
    assert parse_hierarchy("") == []


def test_resource_id_with_no_local_name_is_none_not_empty() -> None:
    # A malformed resource-id ending in `/` must yield identifier None, not "" (which no selector
    # matches yet is falsy-but-present).
    xml = '<hierarchy><node resource-id="com.app:id/" class="android.widget.View" bounds="[0,0][1,1]" /></hierarchy>'
    (el,) = parse_hierarchy(xml)
    assert el["identifier"] is None


def test_tap_resolves_frame_center() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    driver = AdbDriver("U", run=run)
    driver.tap({"id": "stable_refresh"})
    # centre of (0,100,200,100) → (100, 150)
    assert calls == [["adb", "-s", "U", "shell", "input", "tap", "100", "150"]]


def test_tap_on_ambiguous_selector_fails_fast() -> None:
    # Two buttons match `traits: [button]`; a single action must not tap "whatever matched first".
    driver = AdbDriver("U", run=lambda a: FIXTURE)
    with pytest.raises(base.AmbiguousSelector):
        driver.tap({"traits": ["button"]})


def test_capabilities_lean_end() -> None:
    caps = AdbDriver("U", run=lambda a: "").capabilities()
    assert base.Capability.SEMANTIC_TAP not in caps  # coordinate actuation
    assert base.Capability.NETWORK not in caps  # no native monitor
    assert base.Capability.SCREENSHOT in caps
    # multiTouch is advertised (BE-0232): the two-finger sendevent sweep, so preflight admits
    # `gestures_multitouch`. The root precondition is enforced at actuation time, not in the set.
    assert base.Capability.MULTI_TOUCH in caps
    assert base.Capability.TEXT_SELECTION in caps  # Ctrl+A / Ctrl+C actuate (BE-0280)


def test_driver_interval_routes_video_and_devicelog_to_adb_starters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # driver_interval is the seam the FileSink uses for a non-simctl backend; it must dispatch
    # `video` → screenrecord and `deviceLog` → logcat, forwarding the driver's serial (and its
    # runner, so the screenrecord pull/rm go through the same injected run).
    seen: dict[str, tuple[object, ...]] = {}

    def fake_screenrecord(serial: str, path: Path, run: object = None, **_: object) -> object:
        seen["video"] = (serial, path, run)
        return intervals.Interval(kind="video", path=path, provider="adb")

    def fake_logcat(serial: str, path: Path, **_: object) -> object:
        seen["deviceLog"] = (serial, path)
        return intervals.Interval(kind="deviceLog", path=path, provider="adb")

    monkeypatch.setattr(intervals, "start_screenrecord", fake_screenrecord)
    monkeypatch.setattr(intervals, "start_logcat", fake_logcat)

    def run(_a: list[str]) -> str:
        return ""

    drv = AdbDriver("U", run=run)
    video = drv.driver_interval("video", Path("/tmp/v.mp4"))
    assert video is not None and video.kind == "video" and video.provider == "adb"
    assert seen["video"] == ("U", Path("/tmp/v.mp4"), run)

    log = drv.driver_interval("deviceLog", Path("/tmp/d.log"))
    assert log is not None and log.kind == "deviceLog"
    assert seen["deviceLog"] == ("U", Path("/tmp/d.log"))

    assert drv.driver_interval("appTrace", Path("/tmp/a.raw")) is None  # no adb analogue


def _scripted(responses: list[str]) -> tuple[object, list[int]]:
    """A run() that returns dump responses in order (one per dump call), holding the last once
    exhausted. Returns (run, calls) where calls[0] counts dump invocations."""
    seq = list(responses)
    calls = [0]

    def run(args: list[str]) -> str:
        if "dump" in args:
            calls[0] += 1
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    return run, calls


def test_query_retries_through_transient_empty() -> None:
    run, calls = _scripted([FIXTURE, NULL_ROOT, FIXTURE])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._EMPTY_BACKOFF_S = 0  # no real sleeping in the test

    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT  # baseline: _max_seen becomes 4
    els = driver.query()  # hits the null-root then recovers to the full tree
    assert len(els) == FIXTURE_ELEMENT_COUNT
    assert calls[0] == 3  # 1 baseline + (1 empty + 1 recovered)


def test_query_does_not_retry_genuinely_sparse_screen() -> None:
    # No richer tree has ever been seen, so an empty dump is taken at face value.
    run, calls = _scripted([NULL_ROOT])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._EMPTY_BACKOFF_S = 0

    assert driver.query() == []
    assert calls[0] == 1  # returned immediately, no retry


def test_query_returns_after_bounded_retries_when_empty_persists() -> None:
    run, calls = _scripted([FIXTURE, NULL_ROOT])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._EMPTY_BACKOFF_S = 0

    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT  # baseline
    calls[0] = 0
    assert driver.query() == []  # gives up and returns the empty tree
    assert calls[0] == 1 + AdbDriver._EMPTY_RETRIES  # initial + bounded retries


def test_describe_uses_resident_fetch_when_configured() -> None:
    # A configured fetch_hierarchy supplies the dump XML directly; the driver parses it exactly as it
    # parses `uiautomator dump` output — same wire format, only the transport changes (BE-0245) — and
    # never shells out to the dump subprocess.
    def run(args: list[str]) -> str:
        raise AssertionError(f"resident path must not shell out: {args}")

    driver = AdbDriver("U", run=run, fetch_hierarchy=lambda _since: HierarchyRead(FIXTURE))
    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT


def test_describe_falls_back_to_dump_on_resident_error() -> None:
    # A resident-channel failure is not a test outcome: the read degrades to today's `uiautomator
    # dump` path rather than failing, so a device where the resident server cannot answer is never
    # left worse off than before.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return FIXTURE if "dump" in args else ""

    def fetch(_since: float | None) -> HierarchyRead:
        raise AdbResidentError("channel down")

    driver = AdbDriver("U", run=run, fetch_hierarchy=fetch)
    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT
    assert any("dump" in c for c in calls)  # fell back to the dump subprocess


def test_describe_uses_dump_when_no_fetch_configured() -> None:
    # Regression net: with no resident fetch the driver behaves exactly as before — the read is the
    # `uiautomator dump` subprocess, unchanged.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return FIXTURE if "dump" in args else ""

    driver = AdbDriver("U", run=run)
    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT
    assert any("dump" in c for c in calls)


def test_query_transient_retry_rides_over_resident_fetch() -> None:
    # The transient-empty retry sits above _describe, so it is transport-agnostic: a mid-transition
    # null-root read over the resident channel is retried just as a dump one is.
    seq = [FIXTURE, NULL_ROOT, FIXTURE]

    def fetch(_since: float | None) -> HierarchyRead:
        return HierarchyRead(seq.pop(0) if len(seq) > 1 else seq[0])

    driver = AdbDriver("U", run=lambda a: "", fetch_hierarchy=fetch)
    driver._EMPTY_BACKOFF_S = 0  # no real sleeping in the test

    assert len(driver.query()) == FIXTURE_ELEMENT_COUNT  # baseline: _max_seen becomes 4
    els = driver.query()  # hits the null-root over the resident channel then recovers
    assert len(els) == FIXTURE_ELEMENT_COUNT


def test_resident_fallback_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    # The fallback is loud (determinism first): a resident-channel failure is logged so a silently
    # degraded, slower read is visible rather than hidden.
    def fetch(_since: float | None) -> HierarchyRead:
        raise AdbResidentError("channel down")

    driver = AdbDriver("U", run=lambda a: FIXTURE, fetch_hierarchy=fetch)
    with caplog.at_level(logging.WARNING):
        driver.query()
    assert any("resident" in r.message.lower() for r in caplog.records)


def test_resident_failure_latches_to_dump_for_the_rest_of_the_lease() -> None:
    # A mid-lease channel death must not re-pay the failed-connect cost (and re-log) on every read:
    # after the first AdbResidentError the driver disables the channel and reads via dump silently.
    fetch_calls = 0

    def fetch(_since: float | None) -> HierarchyRead:
        nonlocal fetch_calls
        fetch_calls += 1
        raise AdbResidentError("channel down")

    driver = AdbDriver("U", run=lambda a: FIXTURE, fetch_hierarchy=fetch)
    driver.query()  # first read: tries the channel, fails, latches off
    driver.query()  # second read: goes straight to dump, no further fetch attempt
    assert fetch_calls == 1


def test_wait_for_is_single_shot() -> None:
    present = AdbDriver("U", run=lambda a: FIXTURE)
    assert present.wait_for({"id": "stable.submit"}) is True
    absent = AdbDriver("U", run=lambda a: NULL_ROOT)
    assert absent.wait_for({"id": "nope"}) is False


def test_wait_until_polls_until_the_element_appears() -> None:
    run, calls = _scripted([NULL_ROOT, NULL_ROOT, FIXTURE])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    assert base.wait_until(driver, {"id": "stable_refresh"}, timeout=5, poll=0) is True
    assert calls[0] >= 3


def test_swipe_command_shape() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).swipe((10, 20), (30, 40))
    assert calls[0] == ["adb", "-s", "U", "shell", "input", "swipe", "10", "20", "30", "40", "300"]


def test_scroll_is_a_non_inertial_pan_with_a_longer_duration() -> None:
    # A directional scroll on Android is a non-inertial `input swipe` drag (BE-0326): the same command
    # as a swipe but over a longer duration (600ms vs the 300ms default), so the list moves with the
    # finger and settles when the gesture ends rather than flinging past the target with momentum.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).scroll((10, 20), (30, 40))
    assert calls[0] == ["adb", "-s", "U", "shell", "input", "swipe", "10", "20", "30", "40", "600"]


def test_viewport_reads_wm_size_not_the_tree_extent() -> None:
    # The `scroll` viewport is the real display via `wm size` (BE-0326), not `screen_size_from_elements`
    # (a lazy list buffers off-screen rows into the tree, overshooting the screen). Cached: one call.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return "Physical size: 1080x2400\n"

    driver = AdbDriver("U", run=run)
    assert driver.viewport() == (1080.0, 2400.0)
    assert driver.viewport() == (1080.0, 2400.0)  # cached
    assert [a for a in calls if a[-1] == "size"] == [["adb", "-s", "U", "shell", "wm", "size"]]


def test_declares_a_read_lag_so_scroll_does_not_misread_a_late_tree() -> None:
    # Android publishes the accessibility update after the gesture has moved the content, so a read
    # taken in between describes the pre-scroll screen (BE-0326). The driver admits that by
    # implementing ReadLagProvider, which is what stops the `scroll` loop reading one unchanged region
    # as the end of content; the budget has to be a real wait, not a nominal zero, to close the window.
    driver = AdbDriver("U", run=lambda args: "")
    assert isinstance(driver, base.ReadLagProvider)
    assert driver.read_lag() > 0.0


def test_viewport_prefers_override_size() -> None:
    # A resized display prints both a Physical and an Override size; the override is the effective one.
    def run(_args: list[str]) -> str:
        return "Physical size: 1080x2400\nOverride size: 720x1600\n"

    assert AdbDriver("U", run=run).viewport() == (720.0, 1600.0)


def test_type_text_passes_value_over_stdin_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # BE-0155: a typed value (which may be a secret / OTP) goes to `adb shell` on
    # stdin, never in the adb argv where `ps` could read it.
    calls: list[tuple[list[str], str]] = []

    def fake_run_text(cmd: list[str], script: str) -> None:
        calls.append((cmd, script))

    monkeypatch.setattr(AdbDriver, "_run_text", staticmethod(fake_run_text))
    AdbDriver("U", run=lambda a: "").type_text("${secrets.password} val")

    ((cmd, script),) = calls
    assert cmd == ["adb", "-s", "U", "shell"]  # no command on the argv
    assert "${secrets.password}" not in " ".join(cmd)  # the secret is not on the command line
    # `input text` splits on spaces, so a space becomes its `%s` escape; the arg is device-quoted.
    assert script == "input text '${secrets.password}%sval'"


def test_delete_text_sends_count_backspaces_in_one_keyevent() -> None:
    # `count` KEYCODE_DEL (67) in a single `input keyevent` call, not one round-trip per char (BE-0265).
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return ""

    AdbDriver("U", run=run).delete_text(3)
    assert calls == [["adb", "-s", "U", "shell", "input", "keyevent", "67", "67", "67"]]


def test_select_all_is_ctrl_a_keycombination() -> None:
    # Ctrl(113)+A(29) chord — the editor "select all" shortcut on a focused field (BE-0265).
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return ""

    AdbDriver("U", run=run).select_all()
    assert calls == [["adb", "-s", "U", "shell", "input", "keycombination", "113", "29"]]


def test_copy_selection_is_ctrl_c_keycombination() -> None:
    # Ctrl(113)+C(31) chord — copies the active selection to the clipboard (BE-0265).
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return ""

    AdbDriver("U", run=run).copy_selection()
    assert calls == [["adb", "-s", "U", "shell", "input", "keycombination", "113", "31"]]


def test_pinch_contacts_spread_level_about_center() -> None:
    # Two fingers level on a line through the centre, `half` out to either side, moving to half*scale.
    start, end = adb.pinch_contacts((100.0, 150.0), 25.0, 2.0)
    assert start == ((75.0, 150.0), (125.0, 150.0))
    assert end == ((50.0, 150.0), (150.0, 150.0))
    # scale < 1 closes the two contacts inward toward the centre (zoom out).
    _, close_end = adb.pinch_contacts((100.0, 150.0), 25.0, 0.4)
    assert close_end == ((90.0, 150.0), (110.0, 150.0))


def test_rotate_contacts_sweep_a_diameter_about_center() -> None:
    # Both fingers start on a horizontal diameter and sweep through `radians` about the centre; a
    # quarter turn (pi/2) takes (±half, 0) offsets to (0, ∓half) — clockwise in screen coords.
    start, end = adb.rotate_contacts((100.0, 150.0), 20.0, math.pi / 2)
    assert start == ((80.0, 150.0), (120.0, 150.0))
    (e0x, e0y), (e1x, e1y) = end
    assert (round(e0x), round(e0y)) == (100, 130)
    assert (round(e1x), round(e1y)) == (100, 170)


def test_sendevent_gesture_cmd_is_two_slot_protocol_b_sweep() -> None:
    # Both contacts go down (slot 0 / slot 1, one BTN_TOUCH), sweep together across the move frames,
    # then lift — one `adb shell` round-trip. steps=1 gives a single move frame landing on `end`.
    cmd = adb.sendevent_gesture_cmd(
        "U", "/dev/input/event1", ((10, 20), (30, 40)), ((0, 20), (40, 40)), steps=1
    )
    assert cmd[:4] == ["adb", "-s", "U", "shell"]
    d = "/dev/input/event1"
    expected = " ; ".join(
        [
            # down: slot 0 then slot 1, each a tracked contact with pressure, then one press.
            f"sendevent {d} 3 47 0",
            f"sendevent {d} 3 57 200",
            f"sendevent {d} 3 53 10",
            f"sendevent {d} 3 54 20",
            f"sendevent {d} 3 58 50",
            f"sendevent {d} 3 47 1",
            f"sendevent {d} 3 57 201",
            f"sendevent {d} 3 53 30",
            f"sendevent {d} 3 54 40",
            f"sendevent {d} 3 58 50",
            f"sendevent {d} 1 330 1",
            f"sendevent {d} 0 0 0",
            # one move frame: both slots to their end points, one SYN.
            f"sendevent {d} 3 47 0",
            f"sendevent {d} 3 53 0",
            f"sendevent {d} 3 54 20",
            f"sendevent {d} 3 47 1",
            f"sendevent {d} 3 53 40",
            f"sendevent {d} 3 54 40",
            f"sendevent {d} 0 0 0",
            # up: lift both slots (tracking id -1 wraps to 2**32-1), release, SYN.
            f"sendevent {d} 3 47 0",
            f"sendevent {d} 3 57 4294967295",
            f"sendevent {d} 3 47 1",
            f"sendevent {d} 3 57 4294967295",
            f"sendevent {d} 1 330 0",
            f"sendevent {d} 0 0 0",
        ]
    )
    assert cmd[4] == expected


def test_sendevent_gesture_interpolates_each_move_frame_in_order() -> None:
    # The default sweep is multi-frame (steps=8) so the platform sees motion and classifies the
    # gesture; the single-frame builder test above cannot catch an interpolation off-by-one. Slot 0
    # sweeps x 0→80, slot 1 holds at 100, over 4 evenly interpolated frames landing exactly on `end`.
    cmd = adb.sendevent_gesture_cmd(
        "U", "/dev/input/event1", ((0, 0), (100, 0)), ((80, 0), (100, 0)), steps=4
    )
    script = cmd[4]
    # 1 down frame + 4 move frames + 1 up frame = 6 SYN_REPORTs.
    assert script.count("sendevent /dev/input/event1 0 0 0") == 6
    xs = [
        ln.split()[-1]
        for ln in script.split(" ; ")
        if ln.startswith("sendevent /dev/input/event1 3 53")
    ]
    # Down (slot0=0, slot1=100), then per frame slot0 at 20/40/60/80 interleaved with slot1 at 100.
    assert xs == ["0", "100", "20", "100", "40", "100", "60", "100", "80", "100"]


def _root_touch_run(calls: list[list[str]]) -> Callable[[list[str]], str]:
    """A runner for a rooted device with a discoverable touchscreen: dump/id/getevent answered,
    every actuation shell recorded."""

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        if args[-2:] == ["id", "-u"]:
            return "0\n"
        if "getevent" in args:
            return GETEVENT_LP
        calls.append(args)
        return ""

    return run


def test_pinch_drives_two_slot_sendevent_sweep_when_root() -> None:
    # BE-0232: a pinch on stable_refresh (frame [0,100][200,200] → centre (100,150), half=25) spreads
    # two contacts to scale*half about the centre, scaled into the 32767 raw range and swept as one
    # two-slot `sendevent` round-trip. Both slots go down; the final frame lands on the spread ends.
    calls: list[list[str]] = []
    AdbDriver("U", run=_root_touch_run(calls)).pinch({"id": "stable_refresh"}, 2.0)
    assert len(calls) == 1
    script = calls[0][4]
    assert calls[0][:4] == ["adb", "-s", "U", "shell"]
    # Down: slot 0 at raw x for pixel 75, slot 1 at raw x for pixel 125 (y raw 2048 for pixel 150).
    assert "sendevent /dev/input/event1 3 47 0 ; sendevent /dev/input/event1 3 57 200" in script
    assert "sendevent /dev/input/event1 3 53 2275" in script  # slot 0 start, pixel x=75
    assert "sendevent /dev/input/event1 3 53 3792" in script  # slot 1 start, pixel x=125
    # End of the sweep: the two contacts spread to pixel x=50 and x=150 (scale 2.0 about centre 100).
    assert script.rstrip().endswith("sendevent /dev/input/event1 0 0 0")
    assert "sendevent /dev/input/event1 3 53 1517" in script  # slot 0 end, pixel x=50
    assert "sendevent /dev/input/event1 3 53 4551" in script  # slot 1 end, pixel x=150


def test_rotate_drives_two_slot_sendevent_sweep_when_root() -> None:
    # BE-0232: the rotate arm has its own geometry closure, so drive it end-to-end (not just pinch).
    # A quarter turn about centre (100,150) sweeps the two contacts from (75,150)/(125,150) to
    # (100,125)/(100,175) — both ends collapse to the centre pixel x (raw 3034), split in y.
    calls: list[list[str]] = []
    AdbDriver("U", run=_root_touch_run(calls)).rotate({"id": "stable_refresh"}, math.pi / 2)
    assert len(calls) == 1
    script = calls[0][4]
    assert "sendevent /dev/input/event1 3 53 2275" in script  # slot 0 start, pixel x=75
    assert "sendevent /dev/input/event1 3 53 3792" in script  # slot 1 start, pixel x=125
    # End of the sweep: both contacts on the centre's raw x, split above/below in raw y.
    assert "sendevent /dev/input/event1 3 54 1707" in script  # slot 0 end, pixel y=125
    assert "sendevent /dev/input/event1 3 54 2389" in script  # slot 1 end, pixel y=175
    assert script.rstrip().endswith("sendevent /dev/input/event1 0 0 0")


def test_two_finger_gesture_fails_on_degenerate_frame() -> None:
    # A zero-size target frame collapses both contacts onto the centre (half=0) — a zero-travel
    # sequence the platform reads as a tap, not a gesture. Fail loudly with the real cause rather
    # than emitting a no-op that later times out on the mirrored value (BE-0232).
    zero_frame = (
        '<hierarchy><node class="android.view.View" bounds="[0,0][1080,2400]">'
        '<node resource-id="gest.zero" class="android.view.View" bounds="[100,100][100,100]" />'
        "</node></hierarchy>"
    )

    def run(args: list[str]) -> str:
        if "dump" in args:
            return zero_frame
        if args[-2:] == ["id", "-u"]:
            return "0\n"
        if "getevent" in args:
            return GETEVENT_LP
        raise AssertionError(f"no actuation should run on a degenerate frame: {args}")

    with pytest.raises(base.UnsupportedAction):
        AdbDriver("U", run=run).pinch({"id": "gest.zero"}, 2.0)


def test_pinch_and_rotate_require_root_no_fallback() -> None:
    # A two-finger gesture cannot be approximated single-touch, so a non-rooted device fails loudly
    # (unlike the double-tap's `input tap` fallback) rather than emitting a degraded gesture (BE-0232).
    def not_root(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        if args[-2:] == ["id", "-u"]:
            return "2000\n"  # a normal shell user, not root
        raise AssertionError(f"no actuation should run on a non-rooted device: {args}")

    driver = AdbDriver("U", run=not_root)
    with pytest.raises(base.UnsupportedAction, match="root"):
        driver.pinch({"id": "stable_refresh"}, 2.0)
    with pytest.raises(base.UnsupportedAction, match="root"):
        driver.rotate({"id": "stable_refresh"}, 1.0)


def test_pinch_fails_when_no_touch_node_even_if_root() -> None:
    # Rooted but no touchscreen node in `getevent` → nothing to drive the two contacts on, so fail
    # loudly rather than silently no-op (there is no single-touch fallback for a gesture).
    def no_node(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        if args[-2:] == ["id", "-u"]:
            return "0\n"
        if "getevent" in args:
            return 'add device 1: /dev/input/event0\n  name: "gpio-keys"\n    KEY (0001): 0072\n'
        raise AssertionError(f"no actuation should run without a touch node: {args}")

    with pytest.raises(base.UnsupportedAction):
        AdbDriver("U", run=no_node).pinch({"id": "stable_refresh"}, 2.0)


def test_select_option_unsupported() -> None:
    # <select> is a web control with no Android-native counterpart, so the backend refuses (BE-0191).
    driver = AdbDriver("U", run=lambda a: FIXTURE)
    with pytest.raises(base.UnsupportedAction):
        driver.select_option({"id": "nav.theme-picker"}, "midnight")


def test_screenshot_writes_capture_bytes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    def fake_capture(cmd: list[str], path: str) -> None:
        captured.append(cmd)
        with open(path, "wb") as f:
            f.write(b"PNG")

    monkeypatch.setattr(adb.Env, "_run_capture", staticmethod(fake_capture))
    out = tmp_path / "shot.png"
    AdbDriver("U", run=lambda a: "").screenshot(str(out))
    assert out.read_bytes() == b"PNG"
    assert captured == [["adb", "-s", "U", "exec-out", "screencap", "-p"]]


def test_parse_hierarchy_malformed_xml_is_empty() -> None:
    # A truncated/garbled <hierarchy> is treated as a transient bad dump, not a crash.
    assert parse_hierarchy("<hierarchy><node bounds=</hierarchy>") == []


# `getevent -lp` on the Android emulator: several identical `virtio_input_multi_touch_*` nodes, of
# which only the lowest-numbered `/dev/input/eventN` is wired to the display (BE-0208), plus a
# non-touch key node that must be ignored. Trimmed to the axes the parser reads.
GETEVENT_LP = """add device 1: /dev/input/event11
  name:     "virtio_input_multi_touch_11"
    events:
        ABS_MT_POSITION_X     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
        ABS_MT_POSITION_Y     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
add device 11: /dev/input/event1
  name:     "virtio_input_multi_touch_1"
    events:
        ABS_MT_POSITION_X     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
        ABS_MT_POSITION_Y     : value 0, min 0, max 32767, fuzz 0, flat 0, resolution 0
add device 12: /dev/input/event0
  name:     "gpio-keys"
    events:
        KEY (0001): 0072  0073  0074
"""


def test_parse_touch_device_picks_lowest_numbered_touch_node() -> None:
    # The emulator exposes many identical multi-touch nodes; only the lowest-numbered eventN reaches
    # the display, so that one is chosen (event1, not the first-listed event11) with its axis maxima.
    dev = adb.parse_touch_device(GETEVENT_LP)
    assert dev == adb.TouchDevice(path="/dev/input/event1", max_x=32767, max_y=32767)


def test_parse_touch_device_none_without_position_axes() -> None:
    # A node with no ABS_MT_POSITION axes is not a touchscreen — nothing to drive, so None (the
    # driver then falls back to `input tap`).
    only_keys = 'add device 1: /dev/input/event0\n  name: "gpio-keys"\n    KEY (0001): 0072\n'
    assert adb.parse_touch_device(only_keys) is None


def test_scale_to_touch_is_proportional_per_axis() -> None:
    # Screen pixels → the device's raw range, proportionally on each axis independently.
    dev = adb.TouchDevice(path="/dev/input/event1", max_x=32767, max_y=32767)
    assert adb.scale_to_touch((540.0, 1200.0), (1080.0, 2400.0), dev) == (16384, 16384)
    assert adb.scale_to_touch((0.0, 0.0), (1080.0, 2400.0), dev) == (0, 0)
    # A point resolved just outside the screen extent is clamped into the device's raw range.
    assert adb.scale_to_touch((1200.0, -10.0), (1080.0, 2400.0), dev) == (32767, 0)


def test_sendevent_double_tap_cmd_is_two_protocol_b_contacts() -> None:
    # Two down/up contacts at the same point, each a protocol-B slot-0 sequence, chained into one
    # `adb shell` round-trip so no JVM start sits between them (BE-0208).
    cmd = adb.sendevent_double_tap_cmd("U", "/dev/input/event1", 3034, 2048)
    assert cmd[:4] == ["adb", "-s", "U", "shell"]
    script = cmd[4]
    d = "/dev/input/event1"
    one_tap = (
        f"sendevent {d} 3 47 0 ; sendevent {d} 3 57 {{tid}} ; "
        f"sendevent {d} 3 53 3034 ; sendevent {d} 3 54 2048 ; "
        f"sendevent {d} 1 330 1 ; sendevent {d} 3 58 50 ; sendevent {d} 0 0 0 ; "
        f"sendevent {d} 3 57 4294967295 ; sendevent {d} 1 330 0 ; sendevent {d} 0 0 0"
    )
    assert script == one_tap.format(tid=100) + " ; " + one_tap.format(tid=101)


def test_double_tap_uses_sendevent_when_root() -> None:
    # BE-0208: on a rooted device with a discoverable touchscreen, a double-tap is a raw `sendevent`
    # sequence — `input tap` starts a JVM per tap, overrunning the double-tap window even chained.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        if args[-2:] == ["id", "-u"]:
            return "0\n"
        if "getevent" in args:
            return GETEVENT_LP
        calls.append(args)
        return ""

    AdbDriver("U", run=run).double_tap({"id": "stable_refresh"})
    # centre (100,150) on the 1080x2400 FIXTURE screen → raw (3034, 2048) in the 32767 range.
    assert len(calls) == 1
    assert calls[0][:4] == ["adb", "-s", "U", "shell"]
    assert "sendevent /dev/input/event1 3 53 3034" in calls[0][4]
    assert "sendevent /dev/input/event1 3 54 2048" in calls[0][4]


def test_double_tap_falls_back_to_input_tap_when_not_root() -> None:
    # Without root, `/dev/input` is not writable, so a non-rooted device keeps the `input tap ; input
    # tap` behaviour (BE-0210) — the sendevent path never regresses it. Both taps in one round-trip.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        if args[-2:] == ["id", "-u"]:
            return "2000\n"  # a normal shell user, not root
        calls.append(args)
        return ""

    AdbDriver("U", run=run).double_tap({"id": "stable_refresh"})
    # Not root → no getevent probe, straight to the `input tap` fallback.
    assert calls == [
        ["adb", "-s", "U", "shell", "input", "tap", "100", "150", ";", "input", "tap", "100", "150"]
    ]


def test_back_sends_keycode_back() -> None:
    # Android's true system back is a key event, not a tap on an on-screen "back" element — the
    # gap BE-0210 closes (a tap on the iOS OS BackButton has no Android peer). No dump: `back` is a
    # pure keyevent that never resolves a selector.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return ""

    AdbDriver("U", run=run).back()
    assert calls == [["adb", "-s", "U", "shell", "input", "keyevent", "4"]]  # KEYCODE_BACK


def test_long_press_is_a_zero_length_swipe_with_duration() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).long_press({"id": "stable_refresh"}, 1.5)
    # centre (100,150) held for 1500ms via a same-point swipe.
    assert calls == [
        ["adb", "-s", "U", "shell", "input", "swipe", "100", "150", "100", "150", "1500"]
    ]


# A 1080x2400 screen whose `target` sits off the current viewport; a scroll brings it into the tree.
_OFFSCREEN = (
    "<hierarchy><node class='android.widget.FrameLayout' bounds='[0,0][1080,2400]'>"
    "<node resource-id='top' class='android.widget.View' bounds='[0,0][1080,100]' />"
    "</node></hierarchy>"
)
_ONSCREEN = (
    "<hierarchy><node class='android.widget.FrameLayout' bounds='[0,0][1080,2400]'>"
    "<node resource-id='top' class='android.widget.View' bounds='[0,0][1080,100]' />"
    "<node resource-id='target' class='android.widget.Button' bounds='[0,200][200,300]' />"
    "</node></hierarchy>"
)


def test_scroll_into_view_scrolls_then_resolves() -> None:
    # BE-0210: a selector that resolves to nothing in the current viewport triggers a bounded
    # scroll-and-re-query, not an immediate failure — a condition wait (no fixed sleep). tap() must
    # swipe (default: up, revealing content below), re-query, then tap the now-resolved centre.
    scrolled = {"done": False}
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return _ONSCREEN if scrolled["done"] else _OFFSCREEN
        if "swipe" in args:
            scrolled["done"] = True
        calls.append(args)
        return ""

    driver = AdbDriver("U", run=run)
    driver._RESOLVE_TIMEOUT_S = (
        0  # the initial (no-scroll) resolve fails fast; scrolling takes over
    )
    driver.tap({"id": "target"})

    kinds = [a[5] for a in calls]  # the `input` subcommand of each shell call (a[4] is "input")
    assert kinds == ["swipe", "tap"]  # scrolled once (up), then tapped
    assert calls[0][6:10] == ["540", "1680", "540", "720"]  # up-swipe: 0.7h→0.3h at screen centre x
    assert calls[1][6:8] == ["100", "250"]  # centre of target (0,200,200,100)


def test_scroll_into_view_fails_deterministically_after_bounded_scrolls() -> None:
    # A selector that never appears still fails — bounded by a retry count, not an unbounded scroll.
    swipes = {"n": 0}

    def run(args: list[str]) -> str:
        if "dump" in args:
            return _OFFSCREEN  # target never renders, however far we scroll
        if "swipe" in args:
            swipes["n"] += 1
        return ""

    driver = AdbDriver("U", run=run)
    driver._RESOLVE_TIMEOUT_S = 0
    with pytest.raises(base.ElementNotFound):
        driver.tap({"id": "target"})
    assert swipes["n"] == AdbDriver._SCROLL_RETRIES  # bounded, then deterministic failure


def test_scroll_into_view_still_fails_fast_on_ambiguity() -> None:
    # Determinism first: if a scroll reveals *two* matches, the tap fails immediately with
    # AmbiguousSelector (never taps the first) — only not-found triggers a scroll, so ambiguity
    # is not caught by the retry loop and no further scroll happens.
    ambiguous = (
        "<hierarchy><node class='android.widget.FrameLayout' bounds='[0,0][1080,2400]'>"
        "<node resource-id='target' class='android.widget.Button' bounds='[0,200][200,300]' />"
        "<node resource-id='target' class='android.widget.Button' bounds='[0,400][200,500]' />"
        "</node></hierarchy>"
    )
    swipes = {"n": 0}

    def run(args: list[str]) -> str:
        if "dump" in args:
            return ambiguous if swipes["n"] else _OFFSCREEN
        if "swipe" in args:
            swipes["n"] += 1
        return ""

    driver = AdbDriver("U", run=run)
    driver._RESOLVE_TIMEOUT_S = 0
    with pytest.raises(base.AmbiguousSelector):
        driver.tap({"id": "target"})
    assert swipes["n"] == 1  # scrolled once, hit ambiguity, stopped — did not keep scrolling


def test_scroll_on_empty_tree_fails_loudly_without_bogus_swipes() -> None:
    # A genuinely empty tree has no screen extent to swipe across; rather than issue zero-length
    # (no-op) swipes and then fail with a misleading "not found after scroll", it fails loudly with
    # the real cause and issues no swipe.
    swipes = {"n": 0}

    def run(args: list[str]) -> str:
        if "dump" in args:
            return NULL_ROOT  # empty tree, always
        if "swipe" in args:
            swipes["n"] += 1
        return ""

    driver = AdbDriver("U", run=run)
    driver._RESOLVE_TIMEOUT_S = 0
    with pytest.raises(base.ElementNotFound, match="空"):
        driver.tap({"id": "target"})
    assert swipes["n"] == 0  # no bogus (0,0) swipe issued


# A tree with the same ids but shifted frames (mid-animation), then it settles.
ANIMATING = FIXTURE.replace("[0,100][200,200]", "[0,110][200,210]")


def _moved(y: int) -> str:
    """FIXTURE with stable_refresh shifted to y — a distinct frame for an animation step."""
    return FIXTURE.replace("[0,100][200,200]", f"[0,{y}][200,{y + 100}]")


class _Clock:
    """A fake monotonic clock: `sleep` advances it, so wall-clock-bounded loops run instantly."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_settle_polls_until_frames_stabilize() -> None:
    # cache FIXTURE → _settle sees ANIMATING (frames moved) → polls → stable.
    run, _ = _scripted([FIXTURE, ANIMATING, ANIMATING])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._SETTLE_POLL_S = 0
    driver.query()  # populate the stable-key cache with FIXTURE
    tree = driver._settle()
    assert len(tree) == FIXTURE_ELEMENT_COUNT


def test_settle_keeps_polling_past_the_old_count_until_frames_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0245: the settle window is bounded by wall-clock, not a fixed read count, so a fast read (the
    # resident channel, ~0.1s) still spans a real fling. A frame that moves for MORE reads than the
    # old 3-poll cap, then rests, must settle on the RESTING frame — the old count-bound would have
    # returned a still-moving frame (the 4th read) and tapped a stale coordinate.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    # cache baseline, then move for 5 reads (past the old cap of 3) before two equal resting reads.
    run, _ = _scripted([FIXTURE, _moved(110), _moved(130), _moved(150), _moved(165), _moved(170), _moved(170)])  # fmt: skip
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._SETTLE_POLL_S = 0.1
    driver._SETTLE_DEADLINE_S = 2.0
    driver.query()  # cache FIXTURE (resting y=100 baseline)
    tree = driver._settle()
    # Settled on the resting frame (y=170), not any moving frame — proves it polled past the old cap.
    assert _by_id(tree, "stable_refresh")["frame"] == (0.0, 170.0, 200.0, 100.0)
    assert clock.t < driver._SETTLE_DEADLINE_S  # returned on stability, before the deadline


def test_settle_gives_up_at_the_wall_clock_deadline_when_never_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A screen that never stops moving must not spin forever: the poll is bounded by a wall-clock
    # deadline (independent of read cost), after which _settle returns the latest tree.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    reads = [0]

    def run(args: list[str]) -> str:
        if "dump" in args:
            reads[0] += 1
            return _moved(100 + reads[0] * 5)  # every read is a new frame — never settles
        return ""

    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._SETTLE_POLL_S = 0.1
    driver._SETTLE_DEADLINE_S = 0.5
    driver.query()  # cache the first frame
    before = reads[0]
    driver._settle()
    # Bounded by the deadline: ~0.5s / 0.1s poll ⇒ a handful of extra reads, then it stops.
    assert clock.t >= driver._SETTLE_DEADLINE_S
    assert 3 < (reads[0] - before) <= 8  # more than the old 3-poll cap, but bounded by wall-clock


def test_settle_defaults_bound_by_wall_clock_not_read_count() -> None:
    # BE-0245: the class pins a wall-clock deadline and a small non-zero poll interval. The interval
    # is no longer 0 (BE-0234's "the slow read paces the loop") because the resident channel's fast
    # read no longer does — the deadline is what bounds the settle window now.
    assert AdbDriver._SETTLE_DEADLINE_S > 0
    assert AdbDriver._SETTLE_POLL_S > 0


# --- the read-lag catch-up barrier: a pan the tree has not published yet ---
#
# Reproduces `smoke (adb)`'s intermittent `gestures` failure, from the numbers its own run artifacts
# recorded: the second `swipe` scrolled the Log form 73px, and every read for the next 1.2s still
# described the pre-swipe screen — so `long_press` was aimed 10px below the target's real bottom edge,
# pressed the gap, and the mirrored value stayed `idle`. The screenshots for those steps are pixel-
# identical, which is what rules out "the value landed late" and pins it on the coordinate.
_SCROLLED_BY = -73  # the residual pan the tree withheld, in device pixels


def _scrolled(dy: int) -> str:
    """FIXTURE with every leaf shifted by `dy` — the whole content scrolled, tree fully caught up."""

    def shift(m: re.Match[str]) -> str:
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        if (x1, y1, x2, y2) == (0, 0, 1080, 2400):
            return m.group(0)  # the root frame is the screen, not scrolled content
        return f'bounds="[{x1},{y1 + dy}][{x2},{y2 + dy}]"'

    return re.sub(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', shift, FIXTURE)


# The tree mid-catch-up: Android republishes node bounds one node at a time, so the first read that
# differs from the pre-pan projection can still carry most of the old frames. `_moved` shifts only
# `stable_refresh`, which is exactly that shape (the failing run's own post-press read looked like it).
_TORN = _moved(100 + _SCROLLED_BY)


_LONG_PRESS_MS = "700"  # `long_press(..., 0.7)` → `round(duration * 1000)`


def _long_press_target(run_calls: list[list[str]]) -> tuple[float, float]:
    """The (x, y) the long press was actually aimed at, from the captured `input swipe`.

    Matched on the long press's own duration rather than by excluding the pan's, so the helper cannot
    silently start reporting a pan's endpoint if `swipe_cmd`'s default duration ever changes.
    """
    swipe = next(c for c in run_calls if "swipe" in c and c[-1] == _LONG_PRESS_MS)
    return float(swipe[-5]), float(swipe[-4])


def _capturing_run(dumps: list[str]) -> tuple[Callable[[list[str]], str], list[list[str]]]:
    """A `run` serving `dumps` in order (holding the last) and recording every command issued."""
    seq = list(dumps)
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        if "dump" in args:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""

    return run, calls


def test_long_press_waits_for_the_tree_to_catch_up_with_the_pan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The regression itself, end to end through the actuator: a pan moved the content, the next two
    # reads still describe the pre-pan screen, and only the third publishes the new frames. The press
    # must be aimed at the published centre. The stale reads agree with each other, so the
    # two-consecutive-equal-reads settle alone accepts them — this is what the catch-up wait adds.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, calls = _capturing_run([FIXTURE, FIXTURE, FIXTURE, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver.query()  # the tree the pan was aimed at
    driver.swipe((10, 300), (10, 100))
    driver.long_press({"id": "stable.submit"}, 0.7)
    # stable.submit is [0,200][200,300] before the pan, so [0,127][200,227] after: centre y 177, not
    # the stale 250 — which in the CI failure is the 10px that fell past the target's bottom edge.
    assert _long_press_target(calls) == (100.0, 177.0)


def test_settle_rides_out_the_torn_read_the_catch_up_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stopping at the first read that merely *differs* is not enough: that read can be the torn one,
    # still carrying the old frame for the element being resolved. The tear is held here for several
    # consecutive reads, so a two-equal-reads check inside the tear would agree with itself and return
    # `stable.submit` at its pre-pan y — only the dwell rides this out.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    torn_reads = [_TORN] * 4
    run, _ = _capturing_run([FIXTURE, FIXTURE, *torn_reads, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    tree = driver._settle()
    assert _by_id(tree, "stable.submit")["frame"] == (0.0, 127.0, 200.0, 100.0)


def test_settled_query_is_the_settle_a_directional_swipe_anchors_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A directional `swipe` / `drag` is the one selector-addressed actuation whose target is resolved
    # above the driver: `_directional_endpoints` computes two coordinates and hands the driver those,
    # not the selector, so the driver's own `_settle` never sees the anchor. Reading through a bare
    # `query()` there anchored the second of two consecutive swipes on the first one's pre-pan frames
    # — the same lag the long-press regression above pins, reached by the one door the catch-up
    # barrier did not cover. `settled_query` is the seam that closes it, so it must wait the barrier
    # out exactly as `_settle` does rather than return the stale tree.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, _ = _capturing_run([FIXTURE, FIXTURE, FIXTURE, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    assert isinstance(driver, base.SettledReadProvider)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    assert _by_id(driver.settled_query(), "stable.submit")["frame"] == (0.0, 127.0, 200.0, 100.0)


def test_reads_the_runner_already_takes_close_the_barrier_for_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The healthy path — and the passing re-run of the same CI job, where the tree published the pan
    # immediately. The runner's own reads between a pan and the next actuator (a `wait`, an `assert`, a
    # post-step capture) are spread over real time, so they satisfy the dwell themselves and the next
    # `_settle` has nothing pending: no extra reads, no wall-clock burned inside the actuator.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, _ = _capturing_run([FIXTURE, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    driver.query()  # a post-step read that already sees the pan: starts the dwell
    clock.sleep(AdbDriver._CATCHUP_DWELL_S + 0.1)  # the gap to the next step
    driver.query()  # a `wait` / `assert` read: same projection, dwell satisfied
    assert driver._catchup is None
    spent = clock.t
    driver._settle()
    assert clock.t == spent  # the actuator itself never slept


def test_an_external_torn_read_does_not_close_the_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A `wait` / `assert` / post-step read can land inside the ~0.37s tear the run artifacts recorded.
    # Closing the barrier there would hand the next actuator a partly-stale tree with no dwell left to
    # ride the tear out, and `_settle`'s two-equal-reads poll would accept it — the same failure this
    # fix exists to stop, reached by another door. So a torn read must leave the barrier open.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, calls = _capturing_run([FIXTURE, _TORN, _TORN, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.swipe((10, 300), (10, 100))
    driver.query()  # an intervening read that lands mid-tear
    assert driver._catchup is not None  # still open: differing is not the same as caught up
    driver.long_press({"id": "stable.submit"}, 0.7)
    assert _long_press_target(calls) == (100.0, 177.0)  # the whole tree, not the torn one


def test_a_degenerate_read_does_not_close_the_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A mid-transition near-empty tree projects to `()`, which differs from every real projection. It
    # must not count as the pan being published, or the barrier is spent on a tree the read path is
    # itself still retrying and the actuator resolves against pre-pan frames. Two such reads spaced
    # past the dwell are what the dwell alone cannot reject — hence the explicit degenerate skip.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    empties = [NULL_ROOT] * 14  # enough to outlive the transient-empty retries on both reads
    run, calls = _capturing_run([FIXTURE, *empties, *([FIXTURE] * 12), _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver._EMPTY_BACKOFF_S = 0
    driver.query()
    driver.swipe((10, 300), (10, 100))
    driver.query()  # a `wait` poll during the transition: degenerate
    clock.sleep(AdbDriver._CATCHUP_DWELL_S + 0.1)
    driver.query()  # the next poll, still degenerate — dwell satisfied, but on nothing real
    assert driver._catchup is not None
    driver.long_press({"id": "stable.submit"}, 0.7)
    assert _long_press_target(calls) == (100.0, 177.0)


def _resident_driver(
    reads: list[tuple[str, float | None]], clocks: list[float | None]
) -> tuple[AdbDriver, list[str]]:
    """A driver reading through the resident channel with read marks (BE-0332 Unit 3).

    `reads` are (xml, event-mark) pairs served one per read (the last held once exhausted); `clocks`
    are the device-clock values the pre-gesture mark capture (`GET /clock`) returns in order. The
    returned `events` list records each clock probe and each actuation in order, so a test can assert
    the mark is taken *before* the gesture fires.
    """
    read_seq = list(reads)
    clock_seq = list(clocks)
    events: list[str] = []

    def fetch(_since: float | None) -> HierarchyRead:
        xml, mark = read_seq.pop(0) if len(read_seq) > 1 else read_seq[0]
        return HierarchyRead(xml, mark)

    def clock() -> float | None:
        events.append("clock")
        return clock_seq.pop(0) if len(clock_seq) > 1 else clock_seq[0]

    def run(args: list[str]) -> str:
        if "input" in args:
            events.append("act")
        return ""

    return AdbDriver("U", run=run, fetch_hierarchy=fetch, fetch_clock=clock), events


def test_a_marked_read_closes_the_catch_up_the_instant_it_postdates_the_actuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The heart of Unit 3. Every post-gesture read here is byte-identical to the pre-gesture one — a tap
    # that changed only a mirrored value moves no frame, so the projection never differs and the dump
    # path's dwell would wait out the whole budget. Only the device event mark advancing proves the
    # update was published: mark 1000 → 1000 is still stale, and 1001 postdates the actuation and is
    # caught up at once, with no dwell.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    driver, _ = _resident_driver(
        reads=[(FIXTURE, 1000.0), (FIXTURE, 1000.0), (FIXTURE, 1001.0)],
        clocks=[1000.0],
    )
    driver.query()  # the pre-gesture resolve read (event mark 1000)
    driver.swipe((10, 300), (10, 100))  # takes the mark 1000 and arms the barrier
    driver.query()  # a post-gesture read carrying the same mark 1000 — still stale
    assert driver._catchup is not None
    started = clock.t
    driver.query()  # mark 1001 postdates the actuation → caught up
    assert driver._catchup is None
    assert clock.t == started  # closed on the mark alone, no dwell spent


def test_a_marked_read_that_never_postdates_falls_through_at_the_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The mark is a genuine condition wait, so the wall-clock budget becomes a ceiling it rarely reaches
    # (Unit 3). But if the device never publishes — every read keeps the stale mark — the ceiling still
    # bounds the wait and gives up loudly, exactly as the dump path does when a pan moved no frame.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    driver, _ = _resident_driver(reads=[(FIXTURE, 1000.0)], clocks=[1000.0])
    driver._READ_LAG_S = 0.5
    driver.query()
    driver.swipe((10, 300), (10, 100))
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        driver._settle()
    assert clock.t >= driver._READ_LAG_S  # the ceiling bounded the wait
    assert driver._catchup is None  # spent, so the next resolve does not pay it again
    assert "read lag" in caplog.text


def test_the_actuation_mark_is_taken_before_the_gesture_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mark must anchor a "before the gesture" instant: taken after the actuation, a read the gesture
    # itself triggered could postdate it and release the barrier on a still-pre-gesture tree.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    driver, events = _resident_driver(reads=[(FIXTURE, 1000.0)], clocks=[1000.0])
    driver.query()
    driver.swipe((10, 300), (10, 100))
    assert events == ["clock", "act"]


def test_read_postdates_actuation_confirms_only_a_genuine_postdate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The predicate the `extract` settle poll consults (Unit 3): false until a read positively postdates
    # the gesture's device mark, then true, and reset false by the next actuation. It is a confirmation,
    # not "is the barrier quiet" — so a fresh driver reads false before any actuation at all.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    driver, _ = _resident_driver(
        reads=[(FIXTURE, 1000.0), (FIXTURE, 1000.0), (FIXTURE, 1001.0)],
        clocks=[1000.0],
    )
    driver.query()
    assert driver.read_postdates_actuation() is False  # nothing confirmed yet
    driver.swipe((10, 300), (10, 100))
    driver.query()  # stale mark
    assert driver.read_postdates_actuation() is False  # a gesture is waiting to publish
    driver.query()  # postdating mark
    assert driver.read_postdates_actuation() is True  # the device confirmed the order
    driver.back()  # a fresh actuation: the prior confirmation is now stale
    assert driver.read_postdates_actuation() is False


def test_read_postdates_actuation_stays_false_when_the_actuation_armed_no_mark_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The regression the predicate must not have: `back` / `tap_point` resolve no target, so they arm no
    # catch-up and there is no device mark to postdate. The barrier being trivially "closed" must NOT
    # read as a confirmed order — otherwise a mutating `extract` step on one of these actuators would
    # bypass the Unit 1 wall-clock barrier it depends on. A later read must not flip it true either.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    driver, _ = _resident_driver(reads=[(FIXTURE, 1000.0), (FIXTURE, 2000.0)], clocks=[1000.0])
    driver.query()
    driver.back()
    assert driver._catchup is None  # nothing armed
    driver.query()  # even a read whose mark advances must not confirm an order never anchored
    assert driver.read_postdates_actuation() is False


def test_read_source_requests_the_pending_actuation_mark_as_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # BE-0332 Unit 4: the device blocks until a read postdates the requested mark, so the host must tell
    # it which mark to wait past — the pending catch-up's actuation mark. A read with no gesture pending
    # requests none, so the device returns at once.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    seen: list[float | None] = []

    def fetch(since: float | None) -> HierarchyRead:
        seen.append(since)
        return HierarchyRead(FIXTURE, 1001.0)

    driver = AdbDriver("U", run=lambda a: "", fetch_hierarchy=fetch, fetch_clock=lambda: 1000.0)
    driver.query()
    assert seen == [None]  # nothing pending → no mark to postdate
    driver.swipe((10, 300), (10, 100))  # takes the mark 1000 and arms the barrier
    driver.query()  # the post-gesture read carries that mark as `since`
    assert seen[-1] == 1000.0


def test_a_pan_with_no_fresh_read_takes_its_baseline_from_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The coordinate form `swipe: { from, to }` reaches the driver with no `query()` in its step, so the
    # cached projection can predate an earlier actuation. Inheriting it is worse than having none: the
    # first post-pan read differs from it, the barrier credits the pan as published, and the pan's own
    # lag goes unwaited. The baseline is therefore re-read when something has actuated since.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    before, after = _moved(150), _scrolled(_SCROLLED_BY)
    # FIXTURE: the read taken before the earlier tap. `before`: that tap's published effect, still
    # pre-pan. `after`: the pan finally published. `before` is held for more reads than a wrongly
    # inherited baseline needs to settle on it, so only a re-read baseline reaches `after`.
    run, calls = _capturing_run([FIXTURE, *([before] * 12), after])
    driver = AdbDriver("U", run=run)
    driver.query()  # cached projection: A
    driver.tap_point((10, 10))  # actuates; the cached projection is now stale
    driver.swipe((10, 300), (10, 100))  # coordinate form: no query in this step
    driver.long_press({"id": "stable.submit"}, 0.7)
    # 250.0 is B's centre — the pre-pan tree. A stale baseline lands there.
    assert _long_press_target(calls) == (100.0, 177.0)


def test_a_second_pan_waits_for_the_first_to_publish_before_taking_its_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two pans back-to-back with no coordinate resolve between them — which is the shape of the very
    # scenario this fix targets, whose two `swipe` steps resolve their endpoints through `query()` and
    # `resolve_unique`, never `_settle`. Under the lag this fix fights, the second pan's baseline read
    # still shows the pre-first-pan screen, so seeding from it makes the first pan's publish look like
    # the second pan landing, and the actuator resolves a tree missing the second pan's delta.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    first, both = _scrolled(-100), _scrolled(-173)  # the first pan's delta, then both pans'
    run, calls = _capturing_run([FIXTURE, *([FIXTURE] * 3), *([first] * 16), both])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.swipe((10, 300), (10, 100))  # pan 1; its publish is still outstanding
    driver.swipe((10, 300), (10, 100))  # pan 2, with no resolve in between
    driver.long_press({"id": "stable.submit"}, 0.7)
    # stable.submit is [0,200][200,300], so centre y 150 with only the first pan published and 77 with
    # both. Landing on 150 means the second pan's delta was never waited for.
    assert _long_press_target(calls) == (100.0, 77.0)


def test_pinch_arms_the_catch_up_wait_like_a_pan() -> None:
    # A zoom moves every frame on screen just as a pan does, so it carries the same publish lag. Left
    # unarmed, a tap after a `pinch` resolves against pre-zoom frames.
    calls: list[list[str]] = []
    driver = AdbDriver("U", run=_root_touch_run(calls))
    driver.pinch({"id": "stable_refresh"}, 2.0)
    assert driver._catchup is not None


def test_tap_arms_the_catch_up_wait_like_a_pan() -> None:
    # A tap can change the layout — open a menu, expand a row, advance a stepper — just as a pan moves
    # it, so a read taken right after can still describe the pre-tap screen. Left unarmed, the next
    # actuator's `_settle` accepts that stale tree and resolves against pre-tap frames — the `gestures`
    # long-press flake this closes (BE-0332 Unit 2). The tree here never changes, so the barrier stays
    # open; the point is only that a tap arms one at all, as the pan actuators already do.
    driver = AdbDriver("U", run=_root_touch_run([]))
    driver.tap({"id": "stable_refresh"})
    assert driver._catchup is not None


def test_long_press_arms_the_catch_up_wait_like_a_pan() -> None:
    # A long press is a center-resolving actuator too, so a following `_settle` must postdate it.
    driver = AdbDriver("U", run=_root_touch_run([]))
    driver.long_press({"id": "stable_refresh"}, 0.5)
    assert driver._catchup is not None


def test_double_tap_arms_the_catch_up_wait_like_a_pan() -> None:
    driver = AdbDriver("U", run=_root_touch_run([]))
    driver.double_tap({"id": "stable_refresh"})
    assert driver._catchup is not None


def test_tap_then_long_press_resolves_against_the_published_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End to end: a tap moves the content, the next reads still describe the pre-tap screen, and only a
    # later read publishes the new frames. The long press that follows must aim at the published
    # centre, not the pre-tap one — the same guarantee a pan already has, now for a tap (BE-0332 Unit
    # 2). stable.submit is [0,200][200,300] before, so centre y 250; after the -73 pan, centre y 177.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, calls = _capturing_run([FIXTURE, FIXTURE, FIXTURE, FIXTURE, _scrolled(_SCROLLED_BY)])
    driver = AdbDriver("U", run=run)
    driver.query()  # the tree the tap was aimed at
    driver.tap({"id": "stable_refresh"})
    driver.long_press({"id": "stable.submit"}, 0.7)
    assert _long_press_target(calls) == (100.0, 177.0)


def test_every_actuator_invalidates_the_cached_tree() -> None:
    # The guard for `_act`: an actuator that keeps using `_run` would leave the cached projection
    # marked current, and the next pan would inherit a baseline predating this actuation.
    calls: list[list[str]] = []
    actuations: list[Callable[[AdbDriver], object]] = [
        lambda d: d.tap({"id": "stable_refresh"}),
        lambda d: d.tap_point((10, 10)),
        lambda d: d.double_tap({"id": "stable_refresh"}),
        lambda d: d.long_press({"id": "stable_refresh"}, 0.1),
        lambda d: d.swipe((10, 300), (10, 100)),
        lambda d: d.scroll((10, 300), (10, 100)),
        lambda d: d.back(),
        lambda d: d.pinch({"id": "stable_refresh"}, 2.0),
        lambda d: d.rotate({"id": "stable_refresh"}, 1.0),
        lambda d: d.delete_text(2),
        lambda d: d.select_all(),
        lambda d: d.copy_selection(),
    ]
    for actuate in actuations:
        driver = AdbDriver("U", run=_root_touch_run(calls))
        driver.query()
        assert driver._tree_current, "a read must mark the cached projection current"
        actuate(driver)
        assert not driver._tree_current, f"{actuate} left the cached projection marked current"


def test_type_text_invalidates_the_cached_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    # `type_text` goes through the static `_run_text`, bypassing `_act`, so it carries its own
    # invalidation — typing changes the screen as much as any tap.
    monkeypatch.setattr(AdbDriver, "_run_text", staticmethod(lambda cmd, script: None))
    run, _ = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.type_text("horse")
    assert not driver._tree_current


def test_catch_up_gives_up_loudly_at_the_lag_budget_when_the_pan_changed_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A pan at the end of the content legitimately moves nothing, so the projection never differs. The
    # wait is bounded by wall-clock and spent once, rather than blocking the run forever — and it says
    # so, because the alternative is the bare `expect` mismatch that cost a full artifact investigation.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, _ = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run)
    driver._READ_LAG_S = 0.5
    driver.query()
    driver.swipe((10, 300), (10, 100))
    with caplog.at_level(logging.WARNING, logger="bajutsu.adb.resident"):
        tree = driver._settle()
    assert clock.t >= driver._READ_LAG_S
    assert _by_id(tree, "stable.submit")["frame"] == (0.0, 200.0, 200.0, 100.0)  # unmoved
    assert driver._catchup is None  # spent, so the next resolve does not pay it again
    # Both causes named, because the driver cannot tell them apart and this very case is the benign
    # one: asserting the lag alone would point an investigator at a bug that never happened. A pan at
    # the end of the content hits this on every `_scroll_into_view` retry, so it is routine, not exotic.
    assert "read lag" in caplog.text
    # The message names the actuator neutrally ("the last gesture" / "moved no frame") because
    # BE-0332 arms this barrier for taps too, not only pans — so it must not misattribute a tap to a pan.
    assert "never published" in caplog.text and "moved no frame" in caplog.text
    # And it says which test it was applying, so the two named causes can be told apart afterwards.
    # This is the dump path, so the answer is about the projection, not a device mark.
    assert "the projection, which never moved" in caplog.text


def test_the_read_lag_warning_reports_the_marks_it_waited_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The warning appeared three times in a failing lane run and settled nothing, because it named two
    # causes and no evidence. On the mark path the numbers are decisive: a newest read mark that never
    # passed the actuation mark means the device published no accessibility event at all.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, _ = _capturing_run([FIXTURE])
    driver = AdbDriver(
        "U",
        run=run,
        fetch_hierarchy=lambda _since: HierarchyRead(FIXTURE, 1000.0),
        fetch_clock=lambda: 4200.0,
    )
    driver._READ_LAG_S = 0.5
    driver.query()
    driver.swipe((10, 300), (10, 100))
    with caplog.at_level(logging.WARNING):
        driver._settle()
    assert "device mark 4200" in caplog.text
    assert "the newest read was 1000 (-3200ms)" in caplog.text


def test_a_tree_that_never_settles_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Falling through the settle deadline hands the next actuator a screen that was still moving. That
    # was silent, so a coordinate resolved off a mid-animation frame looked exactly like a good one.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    moving = [FIXTURE.replace("0,100][200,200", f"0,{100 + n}][200,{200 + n}") for n in range(200)]
    run, _ = _capturing_run(moving)
    driver = AdbDriver("U", run=run)
    driver.query()
    with caplog.at_level(logging.WARNING):
        driver._settle()
    assert "the tree was still changing after" in caplog.text


def test_a_non_resolving_actuator_does_not_arm_the_catch_up_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The barrier postdates a *resolved* target's layout (BE-0332 Unit 2), so it is scoped to the
    # actuators that resolve a coordinate from the tree. `tap_point` takes raw coordinates and `back`
    # has no target at all, so neither arms — that waits for the resident reader's read mark, which
    # postdates *every* actuation (BE-0332 Unit 3). Arming here would only steal a following pan's
    # fresh baseline. The center-resolving taps that *do* arm are covered by the `_arms_` tests above.
    clock = _Clock()
    monkeypatch.setattr(adb_driver_mod, "time", clock)
    run, _ = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run)
    driver.query()
    driver.tap_point((10, 10))
    assert driver._catchup is None
    driver.back()
    assert driver._catchup is None


def test_read_lag_default_spans_the_measured_ci_lag() -> None:
    # Sized above the >1.2s lag the failing CI run recorded, with margin, because it is only ever spent
    # on a read that still matches the pre-pan screen.
    assert AdbDriver._READ_LAG_S >= 2.0


def test_checked_serial_accepts_real_serials() -> None:
    # Concrete serials, the `booted` alias, and IP:port `adb connect` targets pass unchanged —
    # every command builder embeds the serial via `_adb`.
    for good in ["emulator-5554", "booted", "192.168.1.5:5555", "usb_serial.01"]:
        assert adb._adb(good, "devices") == ["adb", "-s", good, "devices"]


def test_checked_serial_rejects_injection() -> None:
    # A serial that could inject an adb option (leading `-`) or reach argv with a shell
    # metacharacter / space is rejected. adb keeps its own error type (`adb.DeviceError`, an
    # exit-2 device fault) even though the underlying policy is shared with serve.
    for bad in ["-s", "--help", "a b", "a;b", "", "x" * 129]:
        with pytest.raises(adb.DeviceError, match="invalid device serial"):
            adb._adb(bad, "devices")


# --- resident UI Automator server command builders (BE-0245) ---


def test_forward_cmd_asks_adb_for_a_free_host_port() -> None:
    # `tcp:0` lets adb pick an unused host port (printed on stdout), so parallel lanes on distinct
    # serials never contend for one fixed port; the device port is the server's fixed loopback port.
    assert adb.forward_cmd("U") == ["adb", "-s", "U", "forward", "tcp:0", "tcp:6790"]
    assert adb.forward_cmd("U", device_port=7000)[-1] == "tcp:7000"


def test_forward_remove_cmd_tears_down_the_host_port() -> None:
    assert adb.forward_remove_cmd("U", 41000) == [
        "adb",
        "-s",
        "U",
        "forward",
        "--remove",
        "tcp:41000",
    ]


def test_reverse_cmd_tunnels_the_same_device_and_host_port() -> None:
    # `adb reverse tcp:<port> tcp:<port>` — device and host port match so the injected
    # BAJUTSU_COLLECTOR URL (http://127.0.0.1:<port>) resolves on-device unchanged (BE-0283).
    assert adb.reverse_cmd("U", 41000) == ["adb", "-s", "U", "reverse", "tcp:41000", "tcp:41000"]


def test_reverse_remove_cmd_tears_down_the_tunnel() -> None:
    assert adb.reverse_remove_cmd("U", 41000) == [
        "adb",
        "-s",
        "U",
        "reverse",
        "--remove",
        "tcp:41000",
    ]


def test_instrument_cmd_starts_the_blocking_serve_test() -> None:
    # `-w` keeps the instrumentation attached (serve() never returns — it holds the warm session);
    # `-e class …#serve` scopes the run to the one method so no other test executes.
    assert adb.instrument_cmd("U") == [
        "adb",
        "-s",
        "U",
        "shell",
        "am",
        "instrument",
        "-w",
        "-e",
        "class",
        "dev.bajutsu.android.server.ResidentServerTest#serve",
        "dev.bajutsu.android.server.test/androidx.test.runner.AndroidJUnitRunner",
    ]


# --- device-side actuation: the host resolves, the device reads the bounds and injects ---


def _recording_act(
    replies: list[object],
) -> tuple[Callable[[adb_driver_mod.ActRequest], bool], list[adb_driver_mod.ActRequest]]:
    """An `ActFn` serving `replies` in order (holding the last) and recording every request.

    A reply of True/False is the device's answer; an `AdbResidentError` instance is raised instead, so
    one helper covers the acted, the stale, and the channel-fault paths.
    """
    seq = list(replies)
    seen: list[adb_driver_mod.ActRequest] = []

    def act(request: adb_driver_mod.ActRequest) -> bool:
        seen.append(request)
        reply = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(reply, AdbResidentError):
            raise reply
        return bool(reply)

    return act, seen


def test_parse_hierarchy_identities_align_with_the_elements() -> None:
    # The device matches on the dump's raw attributes, so the identity must be verbatim — not the
    # `identifier` `_to_element` strips the package prefix off. Both lists walk the same nodes, so
    # element i is named by identity i; a drift here would address the wrong node on the device.
    els, identities = adb_driver_mod.parse_hierarchy_with_identities(FIXTURE)
    assert len(els) == len(identities) == FIXTURE_ELEMENT_COUNT
    submit = next(i for i, e in enumerate(els) if e["identifier"] == "stable.submit")
    assert identities[submit] == ("stable.submit", "sent", "送信", "android.widget.Button")
    refresh = next(i for i, e in enumerate(els) if e["identifier"] == "stable_refresh")
    # The element's identifier is stripped; the identity keeps the package-qualified resource-id.
    assert identities[refresh][0] == "com.bajutsu.showcase.android.views:id/stable_refresh"


def test_tap_goes_to_the_device_and_injects_no_coordinate() -> None:
    # The point of the device path: the host resolves the element and sends its identity, and no
    # `input tap` is ever issued — so no coordinate computed here can go stale before the touch lands.
    act, seen = _recording_act([True])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.tap({"id": "stable.submit"})
    assert [r.kind for r in seen] == ["tap"]
    assert seen[0].identity == ("stable.submit", "sent", "送信", "android.widget.Button")
    assert (seen[0].index, seen[0].count) == (0, 1)
    assert not [c for c in calls if "input" in c]


def test_long_press_carries_its_duration_to_the_device() -> None:
    # The device has no press-and-hold either, so it needs the hold length to pace its own zero-length
    # swipe. Sent in milliseconds, the unit the resident server counts steps in.
    act, seen = _recording_act([True])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.long_press({"id": "stable.submit"}, 0.7)
    assert [(r.kind, r.duration_ms) for r in seen] == [("longPress", 700)]
    assert not [c for c in calls if "input" in c]


def test_double_tap_stays_off_the_device_path() -> None:
    # Measured on the lane, not assumed: routed through `/act` as two in-process `UiDevice.click`
    # calls, `gestures` failed with `doubletap.value` still 0 — `click` settles between the two, so
    # the pair lands outside the platform's double-tap window that this actuator exists to hit. The
    # device never sees a doubleTap request, however healthy the channel.
    act, seen = _recording_act([True])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.double_tap({"id": "stable.submit"})
    assert seen == []
    assert [c for c in calls if "input" in c or "sendevent" in c]


def test_a_stale_reply_re_resolves_then_falls_back_to_the_coordinate_path() -> None:
    # `stale` means the screen moved between the host's resolve and the device's, so re-resolving is
    # the fix. A target that never settles must not hang or fail: after a bounded number of attempts
    # the gesture takes the coordinate path, leaving the run no worse off than before this existed.
    act, seen = _recording_act([False])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.tap({"id": "stable.submit"})
    assert len(seen) == AdbDriver._STALE_MAX_ATTEMPTS  # bounded, not a spin
    assert [c for c in calls if "input" in c]  # then the coordinate tap really did fire


def test_a_channel_fault_degrades_to_the_coordinate_tap_and_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # An older server answers 404 for /act while still serving reads, which reaches the driver as an
    # AdbResidentError. The gesture must still happen, and the degrade must be visible — but once, not
    # on every tap, so a long run's log stays readable.
    act, seen = _recording_act([AdbActUnsupported("no /act endpoint")])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    with caplog.at_level(logging.WARNING):
        driver.tap({"id": "stable.submit"})
        driver.tap({"id": "stable.submit"})
    assert len(seen) == 1  # probed once for the lease, not once per gesture
    assert len([c for c in calls if "input" in c]) == 2  # both taps still happened
    assert caplog.text.count("resident actuation unavailable") == 1


def test_without_the_channel_the_coordinate_tap_is_unchanged() -> None:
    # The dump path (no `act`) keeps today's behavior byte-for-byte: one `input tap` at the frame
    # centre, with nothing new attempted first.
    run, calls = _capturing_run([FIXTURE])
    AdbDriver("U", run=run).tap({"id": "stable.submit"})
    tap = next(c for c in calls if "tap" in c)
    assert tap == ["adb", "-s", "U", "shell", "input", "tap", "100", "250"]


def test_a_device_tap_arms_the_catch_up_barrier_like_a_coordinate_tap() -> None:
    # The gesture happened, so the same bookkeeping must follow it: the cached tree is stale and the
    # next coordinate resolve has to postdate this tap. Left unarmed, the device path would quietly
    # drop the guarantee the coordinate path spent BE-0332 establishing.
    act, _ = _recording_act([True])
    run, _calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.tap({"id": "stable.submit"})
    assert driver._catchup is not None


def test_an_element_from_a_later_read_than_its_peers_falls_back_instead_of_crashing() -> None:
    # `_resolve` re-queries on a transient not-found and `_scroll_into_view` re-settles, so the element
    # handed back can belong to a later read than the tree the caller settled — and the identity map is
    # rebuilt by every read. Counting peers against the stale tree found none, and taking the element's
    # ordinal in that empty list raised ValueError straight out of the actuator. The gesture must
    # degrade to coordinates instead.
    act, seen = _recording_act([True])
    # A degenerate first read forces the transient-empty retry, so the element resolves from read two.
    run, calls = _capturing_run([NULL_ROOT, FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.query()  # seed the richest-seen count so the null root counts as transient
    driver.tap({"id": "stable.submit"})
    assert [c for c in calls if "input" in c] or seen  # acted or degraded — never raised


def test_a_missing_act_endpoint_is_probed_once_per_lease_not_once_per_tap() -> None:
    # An older server 404s /act while serving reads. Retrying on every gesture would spend a failed
    # round trip *and* a second settle-and-resolve per tap, on the lane BE-0234 exists to keep fast.
    # One probe settles it for the lease.
    act, seen = _recording_act([AdbActUnsupported("no /act endpoint")])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    for _ in range(4):
        driver.tap({"id": "stable.submit"})
    assert len(seen) == 1  # probed once, then never again
    assert len([c for c in calls if "input" in c]) == 4  # every tap still happened


def test_a_transient_actuation_fault_keeps_the_channel_for_the_next_gesture() -> None:
    # A socket blip is not a missing endpoint. Latching on it would hand every later gesture of the
    # lease back to the coordinate path — under exactly the unsettled conditions that produced the
    # blip, and the read channel makes the opposite choice for the same reason. Degrade this gesture,
    # keep the channel: the next tap must reach the device again.
    act, seen = _recording_act([AdbResidentError("connection reset"), True])
    run, calls = _capturing_run([FIXTURE])
    driver = AdbDriver("U", run=run, act=act)
    driver.tap({"id": "stable.submit"})  # blips, falls back to the coordinate tap
    driver.tap({"id": "stable.submit"})  # and the channel is still tried
    assert len(seen) == 2
    assert len([c for c in calls if "input" in c]) == 1  # only the blipped gesture used coordinates
