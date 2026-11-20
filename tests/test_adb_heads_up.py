"""The adb driver reads, reports, and clears an Android heads-up notification (BE-0496).

The dumps under `fixtures/be0496/` were recorded from the resident server on Pixel emulators at API
33, 34, and 35 (Unit 1): the showcase's Permissions tab with a heads-up notification pinned over it,
the same screen with none, and the expanded notification shade drawing the same notification row.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bajutsu.common.backend_cli import adb_resident
from bajutsu.common.drivers import base
from bajutsu.common.drivers.adb import (
    BANNER_GUARD_OFF_NOTE,
    ActOutcome,
    ActRequest,
    AdbDriver,
    AdbResidentError,
    HeadsUpBanner,
    HierarchyRead,
    heads_up_banner,
    slice_hierarchy_root,
)
from bajutsu.common.drivers.adb._functions import _parse_wm_density
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator.actions.handlers.gestures import _tap_with_recovery

_FIXTURES = Path(__file__).parent / "fixtures" / "be0496"
_TEXT = "Showcase notification — A heads-up banner over the app"
# The API 34 banner, and a control the fixture's app draws underneath it.
_BANNER: base.Frame = (42.0, 153.0, 996.0, 218.0)
_UNDER: base.Selector = {"label": "Notifications"}
_CLEAR_OF_IT: base.Selector = {"id": "perm_requestNotif"}


def _xml(name: str) -> str:
    return (_FIXTURES / f"{name}.xml").read_text()


def _read(xml: str, mark: float) -> HierarchyRead:
    """A resident read of `xml`, narrowed exactly as `ResidentServer` narrows one."""
    root, narrowed, banner = adb_resident.narrowed_root(xml)
    return HierarchyRead(xml, mark, root=root, narrowed=narrowed, heads_up=banner)


class _Device:
    """A device showing the API 34 Permissions tab, with or without its heads-up notification.

    A swipe that starts on the banner and ends above it dismisses it, as SystemUI does once the
    flick travels the scaled distance; `stubborn` keeps it up whatever is swiped, and `swipes_to_clear`
    makes the first swipes miss, as one delivered while the banner was still sliding in would.
    """

    def __init__(self, *, banner: bool = True, api: int = 34, frame: base.Frame = _BANNER) -> None:
        self.banner = banner
        self.api = api
        self.frame = frame
        self.stubborn = False
        self.swipes_to_clear = 1
        # A fresh banner pins itself again after this many reads with none, as a stream would.
        self.reappear_after: int | None = None
        self._clear_reads = 0
        self.calls: list[list[str]] = []
        self.reads = 0
        self._mark = 1000.0

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[-2:] == ["wm", "density"]:
            return "Physical density: 420\n"
        if args[-2:] == ["wm", "size"]:
            return "Physical size: 1080x2400\n"
        if "swipe" in args:
            x1, y1, _x2, y2 = (float(v) for v in args[args.index("swipe") + 1 :][:4])
            on_banner = base.contains(self.frame, (x1, y1, 0.0, 0.0)) and y2 < self.frame[1]
            if self.banner and on_banner and not self.stubborn:
                self.swipes_to_clear -= 1
                if self.swipes_to_clear <= 0:
                    self.banner = False
        return ""

    def fetch(self, _since: float | None) -> HierarchyRead:
        self.reads += 1
        self._mark += 1
        if not self.banner and self.reappear_after is not None:
            self._clear_reads += 1
            if self._clear_reads > self.reappear_after:
                self.banner, self._clear_reads = True, 0
        return _read(
            _xml(f"heads_up_api{self.api}" if self.banner else "no_banner_api34"), self._mark
        )

    def gestures(self, kind: str) -> list[list[str]]:
        return [c for c in self.calls if kind in c]


def _driver(device: _Device) -> AdbDriver:
    driver = AdbDriver("U", run=device.run, fetch_hierarchy=device.fetch)
    # Keep the clearance wait short: these tests exercise its shape, not the measured budget.
    driver._BANNER_CLEARANCE_S = 0.6
    driver._BANNER_RESWIPE_S = 0.2
    driver._BANNER_POLL_S = 0.01
    return driver


# --- Unit 2: the frame and text, read from the resident dump --------------------------------------


@pytest.mark.parametrize(
    ("fixture", "frame"),
    [
        ("heads_up_api33", (42.0, 157.0, 996.0, 230.0)),
        ("heads_up_api34", (42.0, 153.0, 996.0, 218.0)),
        ("heads_up_api35", (42.0, 84.0, 996.0, 218.0)),
    ],
)
def test_narrowing_reads_the_heads_up_and_still_drops_every_systemui_window(
    fixture: str, frame: base.Frame
) -> None:
    root, narrowed, banner = adb_resident.narrowed_root(_xml(fixture))
    assert narrowed is True
    assert banner == HeadsUpBanner(frame=frame, text=_TEXT)
    assert root is not None
    assert not [n for n in root.iter("node") if n.get("package") == "com.android.systemui"]


@pytest.mark.parametrize("api", [33, 34, 35])
def test_the_expanded_shade_drawing_the_same_row_is_no_banner(api: int) -> None:
    _root, _narrowed, banner = adb_resident.narrowed_root(_xml(f"shade_expanded_api{api}"))
    assert banner is None


def test_a_screen_with_only_the_status_bar_is_no_banner() -> None:
    assert adb_resident.narrowed_root(_xml("no_banner_api34"))[2] is None


def test_a_row_with_no_area_reads_as_no_banner_yet() -> None:
    xml = _xml("heads_up_api34").replace('bounds="[42,153][1038,371]"', 'bounds="[42,153][42,153]"')
    root = slice_hierarchy_root(xml)
    assert root is not None
    assert heads_up_banner(root) is None


def test_the_app_tree_is_the_same_with_or_without_the_banner() -> None:
    def ids(name: str) -> list[str | None]:
        driver = AdbDriver(
            "U", run=lambda _a: "", fetch_hierarchy=lambda _s: _read(_xml(name), 1.0)
        )
        return [e["identifier"] for e in driver.query()]

    assert ids("heads_up_api34") == ids("no_banner_api34")


def test_wm_density_parses_to_pixels_per_dp() -> None:
    assert _parse_wm_density("Physical density: 420\n") == 2.625
    assert _parse_wm_density("Physical density: 420\nOverride density: 320\n") == 2.0
    with pytest.raises(ValueError, match="wm density"):
        _parse_wm_density("garbage")


# --- Unit 2: answering from the current tree, and the fallback -----------------------------------


def test_the_frame_is_a_fresh_read_every_time() -> None:
    # The orchestrator polls between swipes and sleeps that actuate nothing; a cached answer would
    # keep a banner that left.
    device = _Device()
    driver = _driver(device)
    driver.query()
    assert driver.notification_banner_frame() == _BANNER
    device.banner = False  # gone with no actuation in between
    reads = device.reads
    assert driver.notification_banner_frame() is None
    assert device.reads == reads + 1


def test_the_check_before_a_literal_gesture_reuses_the_current_tree() -> None:
    # The resolve-free gestures read only when an actuation retired the tree.
    device = _Device()
    driver = _driver(device)
    driver.query()
    device.banner = False  # the banner left while nothing read
    reads = device.reads
    driver.tap_point((540.0, 2000.0))  # far below the banner: nothing to clear
    assert device.reads == reads


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def test_the_sweep_confirms_a_banner_that_takes_a_few_reads_to_clear() -> None:
    from bajutsu.common.orchestrator import AlertEvent
    from bajutsu.common.orchestrator.loop._functions import _clear_notification_banner

    device = _Device()
    device.swipes_to_clear = 1
    lingering = {"reads": 2}
    settled = device.fetch

    def fetch(since: float | None) -> HierarchyRead:
        # The banner animates out over the first polls after the swipe, then is gone.
        if not device.banner and lingering["reads"] > 0:
            lingering["reads"] -= 1
            device.banner = True
            read = settled(since)
            device.banner = False
            return read
        return settled(since)

    driver = AdbDriver("U", run=device.run, fetch_hierarchy=fetch)
    assert _clear_notification_banner(driver, _Clock()) == AlertEvent(kind="notificationBanner")
    assert len(device.gestures("swipe")) == 1


def test_the_sweep_does_not_flick_at_a_banner_gone_since_the_last_read() -> None:
    from bajutsu.common.orchestrator.loop._functions import _clear_notification_banner

    device = _Device()
    driver = _driver(device)
    driver.query()  # the last read still showed the banner
    device.banner = False  # it timed out while nothing actuated
    assert _clear_notification_banner(driver, _Clock()) is None
    assert device.gestures("swipe") == []


def test_the_dump_fallback_sees_no_banner_and_says_so_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A lease that never had the server chose the dump, so the line is informational.
    driver = AdbDriver("U", run=lambda _a: _xml("heads_up_api34"))
    with caplog.at_level(logging.INFO):
        driver.query()
        driver.query()
        assert driver.notification_banner_frame() is None
    said = [r for r in caplog.records if r.message == BANNER_GUARD_OFF_NOTE]
    assert [r.levelno for r in said] == [logging.INFO]
    assert driver.drain_channel_notes() == [BANNER_GUARD_OFF_NOTE]
    assert base.Capability.HANDLE_NOTIFICATION_BANNER in driver.capabilities()


def test_a_lost_resident_channel_warns_that_the_guard_is_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    driver = AdbDriver(
        "U", run=lambda _a: _xml("heads_up_api34"), channel_start_failure="server did not start"
    )
    with caplog.at_level(logging.INFO):
        driver.query()
    said = [r for r in caplog.records if r.message == BANNER_GUARD_OFF_NOTE]
    assert [r.levelno for r in said] == [logging.WARNING]


def test_the_capability_is_declared_and_the_scale_is_the_density() -> None:
    device = _Device()
    driver = _driver(device)
    assert base.Capability.HANDLE_NOTIFICATION_BANNER in driver.capabilities()
    assert isinstance(driver, base.DisplayScaleProvider)
    assert isinstance(driver, base.BannerDismissalReporter)
    assert driver.display_scale() == 2.625
    assert driver.display_scale() == 2.625
    assert len(device.gestures("density")) == 1  # cached for the run


# --- Unit 4: the swipe distance in the device's own unit -----------------------------------------


def test_both_swipe_distances_scale() -> None:
    frame: base.Frame = (0.0, 200.0, 300.0, 100.0)
    assert base.notification_banner_swipe_points(frame) == ((150.0, 250.0), (150.0, 180.0))
    assert base.notification_banner_swipe_points(frame, 2.0) == ((150.0, 250.0), (150.0, 160.0))
    # The top margin scales too: a banner flush with the screen's top clamps to 8 units, not 8 px.
    high: base.Frame = (0.0, 30.0, 300.0, 120.0)
    assert base.notification_banner_swipe_points(high, 2.0) == ((150.0, 90.0), (150.0, 16.0))


# --- Unit 5: clearing a banner before the gesture it would take ---------------------------------


def test_a_covering_banner_is_cleared_before_the_tap_and_the_target_resolved_again() -> None:
    device = _Device()
    driver = _driver(device)
    driver.tap(_UNDER)
    swipes, taps = device.gestures("swipe"), device.gestures("tap")
    assert len(swipes) == 1
    assert device.calls.index(swipes[0]) < device.calls.index(taps[0])
    # The tap went to the target's center on the screen the banner left.
    assert taps[0][-2:] == ["540", "330"]
    assert driver.drain_banner_dismissals() == [_TEXT]
    assert driver.drain_banner_dismissals() == []
    assert [a.gesture for a in driver.drain_actuations().records] == ["swipe", "tap"]


def test_a_banner_that_covers_nothing_the_tap_touches_is_left_alone() -> None:
    device = _Device()
    driver = _driver(device)
    driver.tap(_CLEAR_OF_IT)
    assert device.gestures("swipe") == []
    assert device.banner is True
    assert driver.drain_banner_dismissals() == []


def test_a_coordinate_swipe_starting_under_the_banner_clears_it_first() -> None:
    device = _Device()
    driver = _driver(device)
    driver.swipe((540.0, 300.0), (540.0, 1500.0))
    swipes = device.gestures("swipe")
    assert len(swipes) == 2
    assert swipes[-1][-5:-1] == ["540", "300", "540", "1500"]  # the scenario's own swipe
    assert driver.drain_banner_dismissals() == [_TEXT]


def test_the_dismissal_flick_itself_is_not_checked_again() -> None:
    # The orchestrator's sweep swipes from the banner's center to above its top: delivered as asked.
    device = _Device()
    driver = _driver(device)
    points = base.notification_banner_swipe_points(_BANNER, driver.display_scale())
    assert points is not None
    driver.swipe(*points)
    assert len(device.gestures("swipe")) == 1
    assert device.banner is False
    assert driver.drain_banner_dismissals() == []


def test_a_missed_swipe_is_sent_again_once_its_dismissal_time_has_passed() -> None:
    device = _Device()
    device.swipes_to_clear = 2
    driver = _driver(device)
    driver.tap(_UNDER)
    assert len(device.gestures("swipe")) == 2
    assert driver.drain_banner_dismissals() == [_TEXT]


def test_a_banner_mid_slide_is_swiped_only_once_its_frame_settles() -> None:
    # API 35 pins its banner at y=84. While it slides in, its row is a short strip at the top edge,
    # too close to the top for a dismissal flick to travel anywhere, so the driver reads again.
    settled: base.Frame = (42.0, 84.0, 996.0, 218.0)
    device = _Device(api=35, frame=settled)
    sliding = HeadsUpBanner(frame=(42.0, 0.0, 996.0, 95.0), text=_TEXT)
    reads = {"n": 0}

    def fetch(since: float | None) -> HierarchyRead:
        reads["n"] += 1
        read = device.fetch(since)
        if reads["n"] <= 3 and read.heads_up is not None:
            return HierarchyRead(
                read.text, read.mark, root=read.root, narrowed=True, heads_up=sliding
            )
        return read

    assert base.notification_banner_swipe_points(sliding.frame, 2.625) is None
    driver = AdbDriver("U", run=device.run, fetch_hierarchy=fetch)
    driver._BANNER_POLL_S = 0.01
    driver.tap_point((540.0, 90.0))  # inside both the sliding strip and the settled banner
    swipes = device.gestures("swipe")
    assert len(swipes) == 1
    assert reads["n"] > 3  # the swipe waited for the settled frame
    assert device.calls.index(swipes[0]) < device.calls.index(device.gestures("tap")[0])
    assert driver.drain_banner_dismissals() == [_TEXT]


def test_a_banner_that_never_clears_fails_the_gesture_by_name() -> None:
    device = _Device()
    device.stubborn = True
    driver = _driver(device)
    with pytest.raises(base.NotificationBannerBlocking, match="Showcase notification"):
        driver.tap(_UNDER)
    assert device.gestures("tap") == []
    assert driver.drain_banner_dismissals() == []


def test_the_blocking_failure_is_no_occlusion_so_the_tap_recovery_never_scrolls() -> None:
    class _Blocked(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            raise base.NotificationBannerBlocking("a notification banner covered the point")

    driver = _Blocked([])
    assert not issubclass(base.NotificationBannerBlocking, base.ElementNotTappable)
    with pytest.raises(base.NotificationBannerBlocking):
        _tap_with_recovery(lambda: driver.tap({"id": "x"}), driver, {"id": "x"})
    assert [k for k, _ in driver.actions if k in {"swipe", "scroll"}] == []


def test_the_device_side_act_clears_a_covering_banner_before_it_sends() -> None:
    # The resident channel's own actuation resolves through the same check, so the device never
    # injects onto SystemUI's window either.
    device = _Device()
    order: list[str] = []
    requests: list[ActRequest] = []

    def run(args: list[str]) -> str:
        if "swipe" in args:
            order.append("swipe")
        return device.run(args)

    def act(request: ActRequest) -> ActOutcome:
        order.append("act")
        requests.append(request)
        return ActOutcome(acted=True, published_mark=None)

    driver = AdbDriver("U", run=run, fetch_hierarchy=device.fetch, act=act)
    driver._BANNER_POLL_S = 0.01
    driver.tap(_UNDER)
    assert order == ["swipe", "act"]
    assert [r.kind for r in requests] == ["tap"]
    assert device.gestures("tap") == []  # no coordinate fallback
    assert driver.drain_banner_dismissals() == [_TEXT]


def test_the_device_side_act_sends_at_once_with_no_banner() -> None:
    device = _Device(banner=False)
    requests: list[ActRequest] = []

    def act(request: ActRequest) -> ActOutcome:
        requests.append(request)
        return ActOutcome(acted=True, published_mark=None)

    driver = AdbDriver("U", run=device.run, fetch_hierarchy=device.fetch, act=act)
    driver.tap(_UNDER)
    assert len(requests) == 1
    assert device.gestures("swipe") == []


def test_a_stream_of_banners_fails_the_gesture_after_the_bound() -> None:
    device = _Device()
    device.reappear_after = 1
    driver = _driver(device)
    with pytest.raises(base.NotificationBannerBlocking, match="3 cleared, another arrived"):
        driver.tap(_UNDER)
    assert len(device.gestures("swipe")) == 3
    assert device.gestures("tap") == []
    # The banners it did clear still reach the report through the step's drain.
    assert driver.drain_banner_dismissals() == [_TEXT] * 3


def test_a_scroll_starting_under_the_banner_clears_it_first() -> None:
    device = _Device()
    driver = _driver(device)
    driver.scroll((540.0, 300.0), (540.0, 1500.0))
    swipes = device.gestures("swipe")
    assert len(swipes) == 2
    assert swipes[-1][-5:-3] == ["540", "300"]  # the scroll itself, after the clear
    assert driver.drain_banner_dismissals() == [_TEXT]


def test_a_read_that_falls_back_mid_clear_confirms_nothing() -> None:
    # The dump cannot see SystemUI, so its "no banner" after the swipe is not a clearance.
    device = _Device()

    def fetch(since: float | None) -> HierarchyRead:
        if not device.banner:
            raise AdbResidentError("connection reset")
        return device.fetch(since)

    def run(args: list[str]) -> str:
        return _xml("no_banner_api34") if "uiautomator" in args else device.run(args)

    driver = AdbDriver("U", run=run, fetch_hierarchy=fetch)
    driver._BANNER_POLL_S = 0.01
    with pytest.raises(base.NotificationBannerBlocking, match="fell back to uiautomator dump"):
        driver.tap(_UNDER)
    assert device.gestures("tap") == []
    assert driver.drain_banner_dismissals() == []


@pytest.mark.parametrize(
    ("before", "after", "said"),
    [
        (
            "expandableNotificationRow",
            "someOtherRow",
            "no com.android.systemui:id/expandableNotificationRow",
        ),
        ('bounds="[42,153][1038,371]"', 'bounds="garbled"', "bounds that do not parse"),
    ],
)
def test_a_pinned_banner_the_guard_cannot_read_is_said_once(
    before: str, after: str, said: str, caplog: pytest.LogCaptureFixture
) -> None:
    from bajutsu.common.drivers.adb import _functions as adb_functions

    adb_functions._heads_up_warned.clear()
    root = slice_hierarchy_root(_xml("heads_up_api34").replace(before, after))
    assert root is not None
    with caplog.at_level(logging.WARNING):
        assert heads_up_banner(root) is None
        assert heads_up_banner(root) is None
    assert sum(said in r.message for r in caplog.records) == 1


def test_a_banner_a_device_side_gestures_reply_carried_is_not_lost() -> None:
    # The reply's post-gesture tree is pending for the next `query()`; the frame answers from it
    # rather than clearing the banner and consuming the tree with no read.
    device = _Device(banner=False)
    driver = _driver(device)
    driver.query()
    driver._seed_from_act(_read(_xml("heads_up_api34"), 5000.0), 4000.0)
    reads = device.reads
    assert driver.notification_banner_frame() == _BANNER
    assert device.reads == reads
    assert driver.query() is not None  # the seed is still there for the next read
    assert device.reads == reads


def test_a_literal_tap_under_a_seeded_banner_clears_it_first() -> None:
    device = _Device(banner=True)
    driver = _driver(device)
    device.banner = False
    driver.query()
    device.banner = True
    driver._seed_from_act(_read(_xml("heads_up_api34"), 5000.0), 4000.0)
    driver.tap_point((540.0, 260.0))
    swipes, taps = device.gestures("swipe"), device.gestures("tap")
    assert len(swipes) == 1
    assert device.calls.index(swipes[0]) < device.calls.index(taps[0])
