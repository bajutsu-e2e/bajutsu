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


def test_double_tap_is_a_single_round_trip() -> None:
    # BE-0210: both taps go through one `adb shell` round-trip (`input tap …; input tap …`) rather
    # than two separate adb invocations, so the adb transport round-trip does not sit between the
    # taps and widen the gap past the platform's double-tap window.
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).double_tap({"id": "stable_refresh"})
    # centre of (0,100,200,100) → (100, 150); one call, both taps chained in the device shell.
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
