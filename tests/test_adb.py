"""Tests for the adb backend: uiautomator dump parsing, commands, coordinate tap, transient retry.

The adb driver is the twin of idb, so these mirror `test_idb.py`: the selector mapping over captured
`uiautomator dump` XML, frame-centre taps, the transient-empty retry, and ambiguous-fails-fast — all
over an injected `run`, no device needed (BE-0007 Unit 7, fast gate).
"""

from __future__ import annotations

import pytest

from bajutsu import adb
from bajutsu.drivers import base
from bajutsu.drivers.adb import AdbDriver, parse_hierarchy

# A realistic dump: a Views native id (package-prefixed), a Compose testTag (verbatim, dotted),
# a disabled button, and a checked switch — exercising the whole selector mapping.
FIXTURE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="0" text="設定" resource-id="com.bajutsu.showcase.android.views:id/stable_refresh"
      class="android.widget.Button" content-desc="" enabled="true" checked="false"
      selected="false" bounds="[0,100][200,200]" />
    <node index="1" text="送信" resource-id="stable.submit" class="android.widget.Button"
      content-desc="送信ボタン" enabled="false" bounds="[0,200][200,300]" />
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
    assert refresh["label"] == "設定"  # no content-desc → falls back to visible text
    assert refresh["value"] == "設定"
    assert refresh["traits"] == ["button"]
    assert refresh["frame"] == (0.0, 100.0, 200.0, 100.0)  # [0,100][200,200] → x,y,w,h

    # Compose testTag: dotted id reproduced verbatim (no package prefix to strip).
    submit = _by_id(els, "stable.submit")
    assert submit["label"] == "送信ボタン"  # content-desc wins over text for the label
    assert submit["value"] == "送信"
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


def test_double_tap_issues_two_taps() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "dump" in args:
            return FIXTURE
        calls.append(args)
        return ""

    AdbDriver("U", run=run).double_tap({"id": "stable_refresh"})
    assert calls == [["adb", "-s", "U", "shell", "input", "tap", "100", "150"]] * 2


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
