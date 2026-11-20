"""Every notification banner the run clears reaches the report as a `notificationBanner` record.

BE-0496 Unit 5: the orchestrator's own sweep now records what it clears at each of its three sites,
and a backend that clears a banner itself before a gesture (`BannerDismissalReporter`) drains its
dismissals into the step that met them. Both are covered against a `FakeDriver`.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from _orch import FakeClock, _scenario

from bajutsu.common.assertions import EvalContext, VisualContext
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.orchestrator import AlertEvent, run_scenario
from bajutsu.common.orchestrator.types import drain_banner_dismissals

_BANNER_FRAME: base.Frame = (8.0, 58.7, 386.0, 78.7)
_SWEPT = AlertEvent(kind="notificationBanner")


def _home() -> base.Element:
    return {
        "identifier": "home",
        "label": "Home",
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


class _ClearingDriver(FakeDriver):
    """A banner the sweep's swipe really clears, so the sweep's own confirmation succeeds."""

    def swipe(self, frm: base.Point, to: base.Point) -> None:
        super().swipe(frm, to)
        self.notification_banner = None


class _ClearsBeforeGestures(FakeDriver):
    """A backend that clears banners itself and takes no interruption policy, as the adb driver.

    Setting the two policy members to None is how a runtime-checkable protocol reads a member as
    absent, so this is not an `InterruptionPolicyTarget`: the drain must not hide behind that guard.
    """

    drain_interruptions = None  # type: ignore[assignment]
    set_interruption_policy = None  # type: ignore[assignment]

    def __init__(self, elements: list[base.Element]) -> None:
        super().__init__(elements)
        self.dismissed: list[str] = []

    def tap(self, sel: base.Selector) -> None:
        self.dismissed.append("Order shipped — Track it")
        super().tap(sel)

    def drain_banner_dismissals(self) -> list[str]:
        drained, self.dismissed = self.dismissed, []
        return drained


def _visual_ctx(tmp_path: Path, prefix: str) -> EvalContext:
    return EvalContext(
        visual=VisualContext(
            screenshot_path=tmp_path / prefix / "shot.png",
            baselines_dir=tmp_path / "baselines",
            writer=RunArtifactWriter(tmp_path, Redactor(None)),
            prefix=prefix,
        )
    )


def test_the_per_step_sweep_records_a_confirmed_dismissal() -> None:
    driver = _ClearingDriver([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "a", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].alerts == [_SWEPT]


def test_an_unconfirmed_swipe_records_nothing() -> None:
    # The banner outlives the sweep's wait: a swipe happened, but nothing was cleared to report.
    driver = FakeDriver([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "b", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert [a.gesture for a in result.steps[0].actuations] == ["swipe"]
    assert result.steps[0].alerts == []


def test_the_step_level_visual_sweep_records_on_the_step(tmp_path: Path) -> None:
    driver = _ClearingDriver([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "c", "steps": [{"assert": [{"visual": {"baseline": "home.png"}}]}]}),
        clock=FakeClock(),
        ctx=_visual_ctx(tmp_path, "00-c"),
    )
    # The capture-time clear took the banner, so the per-step sweep after it found nothing.
    assert result.steps[0].alerts == [_SWEPT]
    assert result.expect_alerts == []


def test_the_expect_phase_visual_sweep_records_on_the_run(tmp_path: Path) -> None:
    driver = _ClearingDriver([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "d", "steps": [], "expect": [{"visual": {"baseline": "home.png"}}]}),
        clock=FakeClock(),
        ctx=_visual_ctx(tmp_path, "00-d"),
    )
    assert result.expect_alerts == [_SWEPT]


def test_the_sweep_scales_its_swipe_by_the_backends_display_scale() -> None:
    class _Dense(_ClearingDriver):
        def display_scale(self) -> float:
            return 2.625

    driver = _Dense([_home()])
    driver.notification_banner = (42.0, 153.0, 996.0, 218.0)
    run_scenario(
        driver,
        _scenario({"name": "e", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    swipes = [arg for kind, arg in driver.actions if kind == "swipe"]
    assert swipes == [base.notification_banner_swipe_points((42.0, 153.0, 996.0, 218.0), 2.625)]


def test_a_driver_dismissal_reaches_the_step_without_an_interruption_policy() -> None:
    driver = _ClearsBeforeGestures([_home()])
    assert not isinstance(driver, base.InterruptionPolicyTarget)
    result = run_scenario(
        driver,
        _scenario({"name": "f", "steps": [{"tap": {"id": "home"}}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].alerts == [
        AlertEvent(label="Order shipped — Track it", kind="notificationBanner")
    ]


def test_a_native_dismissal_during_a_web_block_step_lands_on_that_step() -> None:
    # The web block's own taps go to the WebView driver; a banner the native driver cleared meanwhile
    # still belongs to this step, drained from the native driver rather than stranded there.
    class _WebBridge:
        def session(self, webview_id: str, frame: base.Frame) -> AbstractContextManager[None]:
            return nullcontext()

        def query_dom(self, webview_id: str) -> list[base.Element]:
            return [
                {
                    "identifier": "go",
                    "label": "Go",
                    "traits": ["button"],
                    "value": None,
                    "frame": (0.0, 0.0, 50.0, 20.0),
                    "nativeZ": None,
                }
            ]

        def tap_element(self, webview_id: str, point: base.Point) -> None:
            pass

        def type_text(self, webview_id: str, text: str) -> None:
            pass

        def scroll_to(self, webview_id: str, element_id: str) -> None:
            pass

    driver = _ClearsBeforeGestures(
        [
            {
                "identifier": "checkout.webview",
                "label": "WebView",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 400.0, 800.0),
                "nativeZ": None,
            }
        ]
    )
    driver.dismissed.append("Your code is 123456")
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "g",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "checkout.webview"},
                            "steps": [{"tap": {"id": "go"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=_WebBridge(),
    )
    assert result.ok, result.failure
    assert AlertEvent(label="Your code is 123456", kind="notificationBanner") in (
        result.steps[0].alerts
    )
    assert driver.drain_banner_dismissals() == []


def test_a_backend_without_the_opt_in_drains_nothing() -> None:
    assert drain_banner_dismissals(FakeDriver([_home()])) == []


def test_a_confirmation_read_on_a_degraded_channel_records_nothing() -> None:
    # A channel that fell back since the swipe may be blind to banners: its "none" proves nothing.
    class _Degraded(_ClearingDriver):
        def drain_channel_notes(self) -> list[str]:
            return []

        def channel_degraded(self) -> str | None:
            return "resident channel lost"

    driver = _Degraded([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "h", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert [a.gesture for a in result.steps[0].actuations] == ["swipe"]
    assert result.steps[0].alerts == []


def test_a_flick_the_backend_refuses_leaves_the_banner_and_the_run_alive() -> None:
    class _Refuses(FakeDriver):
        def swipe(self, frm: base.Point, to: base.Point) -> None:
            raise base.NotificationBannerBlocking("the banner moved")

    driver = _Refuses([_home()])
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "i", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert result.steps[0].alerts == []
