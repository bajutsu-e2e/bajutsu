"""The per-step actuation record: what the driver actually did to the screen.

A step's outcome used to name its action and its duration and nothing about the gesture. These tests
pin the record that closes that gap — the coordinate a tap injected, the endpoints a swipe travelled,
the channel that carried each gesture — as behavior, on the fast gate with no device.

`FakeDriver` records real coordinates (its device is memory, so it chooses its own touch points), which
is what lets these assert exact geometry: the expected point is computed here from the seeded frame,
independently of the driver.
"""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import GUARD_LABEL, AlertingDriver, el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.actuation import MAX_RECORDS, Actuation, ActuationLog
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertGuardConfig, run_scenario
from bajutsu.common.orchestrator.actions.handlers._gesture_math import _scroll_gesture

_BUTTON = el("settings.open", frame=(20.0, 100.0, 80.0, 40.0))
_TITLE = el("home.title", frame=(0.0, 0.0, 200.0, 60.0))


def _run(steps: list[dict[str, object]], driver: FakeDriver, **kw: object) -> list[list[Actuation]]:
    """Run `steps` and return each step outcome's actuations, in step order."""
    result = run_scenario(
        driver,
        _scenario({"name": "record", "steps": steps}),
        clock=FakeClock(),
        **kw,  # type: ignore[arg-type]  # pass-through for the few tests that need alert_guard
    )
    return [list(step.actuations) for step in result.steps]


# --- geometry: the record states the point the driver chose ---


def test_tap_records_the_resolved_frame_center_and_identifier() -> None:
    driver = FakeDriver([_BUTTON, _TITLE])

    (tap,) = _run([{"tap": {"id": "settings.open"}}], driver)[0]

    assert tap.gesture == "tap"
    assert tap.via == "coordinate"
    assert tap.unit == "point"
    assert tap.points == ((60.0, 120.0),)  # the seeded frame's centre, computed here independently
    assert tap.frame == (20.0, 100.0, 80.0, 40.0)
    assert tap.target == "settings.open"


def test_long_press_records_its_duration() -> None:
    driver = FakeDriver([_BUTTON])

    (press,) = _run([{"longPress": {"sel": {"id": "settings.open"}, "duration": 1.5}}], driver)[0]

    assert (press.gesture, press.duration_s) == ("longPress", 1.5)
    assert press.points == ((60.0, 120.0),)


def test_directional_swipe_records_the_endpoints_the_handler_computed() -> None:
    driver = FakeDriver([_BUTTON, _TITLE])

    (scroll,) = _run([{"swipe": {"on": {"id": "settings.open"}, "direction": "up"}}], driver)[0]

    # The same endpoints the gesture handler derives, recomputed here from the seeded screen.
    screen = driver.viewport()
    frm, to = _scroll_gesture(base.frame_center(_BUTTON["frame"]), "up", None, screen)
    assert scroll.gesture == "scroll"  # a directional swipe routes to `driver.scroll`
    assert scroll.points == (frm, to)


def test_pinch_records_its_scale_and_the_resolved_frame() -> None:
    driver = FakeDriver([_BUTTON])

    (pinch,) = _run([{"pinch": {"sel": {"id": "settings.open"}, "scale": 2.0}}], driver)[0]

    assert (pinch.gesture, pinch.scale) == ("pinch", 2.0)
    assert pinch.frame == (20.0, 100.0, 80.0, 40.0)


def test_a_scrolled_screen_records_the_translated_centre() -> None:
    """The recorded point is in `query()` space, not the untranslated space resolution runs against.

    Driven straight at the driver: this is a property of where the record takes its frame from, and
    going through a scenario would only add the gesture handler's endpoint arithmetic on top of it.
    """
    driver = FakeDriver([el("row.9", frame=(0.0, 900.0, 200.0, 40.0))], viewport=(200.0, 400.0))

    driver.scroll((100.0, 300.0), (100.0, 100.0))  # pans the content offset down by 200
    driver.tap({"id": "row.9"})

    _scroll, tap = driver.drain_actuations().records
    # The row's seeded frame is unchanged at y=900; the offset puts it at y=700 on screen, and that
    # is where a touch actually has to land.
    assert tap.frame == (0.0, 700.0, 200.0, 40.0)
    assert tap.points == ((100.0, 720.0),)


# --- attribution: each step carries exactly its own actuations ---


def test_each_step_carries_only_its_own_actuations() -> None:
    driver = FakeDriver([_BUTTON, _TITLE])

    steps = _run(
        [
            {"tap": {"id": "settings.open"}},
            {"assert": [{"exists": {"id": "home.title"}}]},
            {"tap": {"id": "home.title"}},
        ],
        driver,
    )

    assert [[a.target for a in step] for step in steps] == [["settings.open"], [], ["home.title"]]


class _FlakyTapDriver(AlertingDriver):
    """Records its tap, then fails it once — an actuation that landed on a step that still failed.

    The realistic shape of the guard's retry: the touch really went to the device (an overlay swallowed
    it), so the record must survive on the step even though the step failed and ran again. The
    swallowing prompt is a real seeded one, so the guard clears it the one way it still can (BE-0402).
    """

    def __init__(self, screen: list[base.Element]) -> None:
        super().__init__(screen)
        self.attempts = 0

    def tap(self, sel: base.Selector) -> None:
        super().tap(sel)  # resolves and records, exactly as the real driver does before it sends
        self.attempts += 1
        if self.attempts == 1:
            raise base.ElementNotFound("a system prompt swallowed the tap")


def test_a_step_retried_after_an_alert_carries_both_attempts_in_order() -> None:
    """The guard dismisses a prompt and the body runs again; both taps really happened."""
    driver = _FlakyTapDriver([el("blocked.button", frame=(0.0, 0.0, 10.0, 10.0))])

    steps = _run(
        [{"tap": {"id": "blocked.button"}}],
        driver,
        alert_guard=AlertGuardConfig(labels=[GUARD_LABEL]),
    )

    # Everything that really reached the device during this step, in order: the tap that failed, the
    # guard's own dismiss (by handle, out of the app's coordinate space, so no target), and the tap
    # that then succeeded.
    assert [(a.gesture, a.target) for a in steps[0]] == [
        ("tap", "blocked.button"),
        ("systemAlert", None),
        ("tap", "blocked.button"),
    ]


def test_the_expect_phase_guard_records_onto_the_scenario_result() -> None:
    """The one actuation with no step to carry it lands on `RunResult.expect_actuations`."""

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "tap":  # the prompt closes and the app finishes what it was blocked on
            d.screen = [el("verified", frame=(0.0, 0.0, 5.0, 5.0))]

    # The in-tree dismiss is the guard path that actuates the app's own screen: an identifier-less
    # button the scenario's own `labels` named, tapped through `Driver.tap` (BE-0315). It is the one
    # guard path that still logs a gesture now the vision fallback is gone (BE-0402); the native
    # path taps out of the app's coordinate space by handle, so it records no in-app actuation.
    prompt = el(None, "OK", ["button"], frame=(10.0, 10.0, 30.0, 30.0))
    driver = FakeDriver([prompt], react=react)

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "expect retry",
                "steps": [{"assert": [{"exists": {"label": "OK"}}]}],
                "expect": [{"exists": {"id": "verified"}}],
            }
        ),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(labels=["OK"]),
    )

    assert result.ok
    # No identifier on the prompt's button, so the record carries the gesture and no authored target.
    assert [(a.gesture, a.target) for a in result.expect_actuations] == [("tap", None)]
    # And it is not double-counted onto the step that ran before the expect phase.
    assert result.steps[0].actuations == []


# --- the redaction boundary: no authored string reaches the record ---


def test_an_element_with_only_a_label_records_no_target() -> None:
    """A label can hold a resolved secret, so it is never a `target` fallback."""
    labelled = el(None, label="Reveal ${secrets.token}", frame=(0.0, 0.0, 40.0, 20.0))
    driver = FakeDriver([labelled])

    (tap,) = _run([{"tap": {"label": "Reveal ${secrets.token}"}}], driver)[0]

    assert tap.target is None
    assert tap.points == ((20.0, 10.0),)  # still localized, by coordinate and frame


def test_a_type_step_records_nothing_derived_from_its_text() -> None:
    field = el("login.password", frame=(0.0, 0.0, 100.0, 20.0))
    driver = FakeDriver([field])

    tap, typed = _run([{"type": {"into": {"id": "login.password"}, "text": "hunter2"}}], driver)[0]

    assert (tap.gesture, tap.via) == ("tap", "coordinate")  # focusing the field
    assert (typed.gesture, typed.via) == ("typeText", "focused")
    assert typed.points == () and typed.target is None
    # Nothing on the record carries the text or its length (7 characters).
    assert 7 not in {typed.duration_s, typed.scale, typed.radians}
    assert "hunter2" not in repr(typed)


# --- the accumulator: a refused attempt, and a truncated record that says it is truncated ---


def test_settle_marks_the_attempt_the_platform_refused() -> None:
    # Without this, a stale-retried tap leaves several identical records and nothing says which one
    # the device honored — the report would render one tap as three.
    log = ActuationLog()
    log.record(Actuation(gesture="tap", via="handle", unit="point", target="ok"))
    log.settle(False)
    log.record(Actuation(gesture="tap", via="handle", unit="point", target="ok"))
    log.settle(True)

    assert [a.accepted for a in log.drain().records] == [False, True]


def test_settle_on_an_empty_log_cannot_corrupt_an_already_drained_record() -> None:
    log = ActuationLog()
    log.record(Actuation(gesture="tap", via="handle", unit="point"))
    drained = log.drain()

    log.settle(False)  # the record it would have stamped is already gone

    assert drained.records[0].accepted is None
    assert log.drain().records == []


def test_a_truncated_log_reports_what_it_dropped() -> None:
    # The cap exists for a consumer that never drains, but the earliest gestures of a step are exactly
    # what "the scroll never reached its target" needs — so a truncated record must say so rather than
    # read as complete.
    log = ActuationLog(maxlen=3)
    for i in range(5):
        log.record(Actuation(gesture="scroll", via="coordinate", unit="pixel", target=f"s{i}"))

    drained = log.drain()

    assert [a.target for a in drained.records] == ["s2", "s3", "s4"]
    assert drained.dropped == 2
    # The count resets with the records, so the next step is not blamed for this one's truncation.
    assert log.drain().dropped == 0


def test_the_default_cap_is_far_above_a_real_step() -> None:
    # A `scroll` step spends up to `maxScrolls` gestures (default 15, author-settable); the cap must
    # sit well clear of that, or an ordinary run would start losing records.
    assert MAX_RECORDS >= 512
