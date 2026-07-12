"""Tests for the adb backend: uiautomator dump parsing, commands, coordinate tap, transient retry.

The adb driver is the twin of idb, so these mirror `test_idb.py`: the selector mapping over captured
`uiautomator dump` XML, frame-centre taps, the transient-empty retry, and ambiguous-fails-fast — all
over an injected `run`, no device needed (BE-0007 Unit 7, fast gate).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu import adb, intervals
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver, parse_hierarchy

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
# too — one more than the three leaf elements, unlike idb's accessibility-only set.
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
    assert base.Capability.SEMANTIC_TAP not in caps  # coordinate actuation, like idb
    assert base.Capability.NETWORK not in caps  # no native monitor
    assert base.Capability.SCREENSHOT in caps


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


def test_scroll_delegates_to_a_real_drag() -> None:
    # A directional scroll on Android is a real `input swipe` drag, so scroll delegates to swipe
    # (BE-0227) — the same command shape, since a drag already scrolls Android scroll views.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).scroll((10, 20), (30, 40))
    assert calls[0] == ["adb", "-s", "U", "shell", "input", "swipe", "10", "20", "30", "40", "300"]


def test_type_text_passes_value_over_stdin_not_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    # BE-0155 parity with idb: a typed value (which may be a secret / OTP) goes to `adb shell` on
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


def test_pinch_and_rotate_unsupported() -> None:
    driver = AdbDriver("U", run=lambda a: FIXTURE)
    with pytest.raises(base.UnsupportedAction):
        driver.pinch({"id": "stable_refresh"}, 2.0)
    with pytest.raises(base.UnsupportedAction):
        driver.rotate({"id": "stable_refresh"}, 1.0)


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


def test_settle_polls_until_frames_stabilize() -> None:
    # cache FIXTURE → _settle sees ANIMATING (frames moved) → polls → stable.
    run, _ = _scripted([FIXTURE, ANIMATING, ANIMATING])
    driver = AdbDriver("U", run=run)  # type: ignore[arg-type]
    driver._SETTLE_POLL_S = 0
    driver.query()  # populate the stable-key cache with FIXTURE
    tree = driver._settle()
    assert len(tree) == FIXTURE_ELEMENT_COUNT


def test_checked_serial_accepts_real_serials() -> None:
    # Concrete serials, the `booted` alias, and IP:port `adb connect` targets pass unchanged —
    # every command builder embeds the serial via `_adb`.
    for good in ["emulator-5554", "booted", "192.168.1.5:5555", "usb_serial.01"]:
        assert adb._adb(good, "devices") == ["adb", "-s", good, "devices"]


def test_checked_serial_rejects_injection() -> None:
    # A serial that could inject an adb option (leading `-`) or reach argv with a shell
    # metacharacter / space is rejected. adb keeps its own error type (`adb.DeviceError`, an
    # exit-2 device fault) even though the underlying policy is shared with idb/serve.
    for bad in ["-s", "--help", "a b", "a;b", "", "x" * 129]:
        with pytest.raises(adb.DeviceError, match="invalid device serial"):
            adb._adb(bad, "devices")
