"""Tests for the reactive native system-alert guard path (BE-0315).

The reactive guard clears SpringBoard prompts automatically, preferring a deterministic native path
built on BE-0316's primitives — `system_alert_labels()` (a read of BE-0316's `/systemAlert/query`)
to see the alert's buttons, then `handle_system_alert()` to tap a policy-named one — over the vision
fallback. Exercised against `FakeDriver`, which advertises `HANDLE_SYSTEM_ALERT` and can be seeded
with alert buttons, so nothing here needs a Simulator; the on-device confirmation is a separate lane.
"""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import AlertEvent, AlertGuardConfig
from bajutsu.orchestrator.types import DEFAULT_DISMISSIVE_LABELS, pick_alert_label


class _LogicalClock:
    """A clock whose only motion is `sleep` advancing logical time (no real waiting)."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


class _Incapable:
    """A driver stub without the HANDLE_SYSTEM_ALERT capability (for the incapable branch)."""

    def capabilities(self) -> set[str]:
        return set()


def _button(label: str) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0, 0, 10, 10),
    }


def _fake_with_alert(labels: list[str], react: object = None) -> FakeDriver:
    driver = FakeDriver([], react=react)  # type: ignore[arg-type]
    driver.system_alert_buttons = [_button(label) for label in labels]
    return driver


def _never_vision(_driver: base.Driver) -> AlertEvent | None:
    raise AssertionError("the vision fallback must not be called on the native path")


# --- pick_alert_label -------------------------------------------------------------------------------


def test_pick_alert_label_returns_first_uniquely_present_candidate() -> None:
    assert pick_alert_label(["Allow", "OK"], ["Don't Allow", "Allow"]) == "Allow"
    assert pick_alert_label(["Grant", "OK"], ["Cancel", "OK"]) == "OK"


def test_pick_alert_label_none_when_no_candidate_present() -> None:
    assert pick_alert_label(["Allow"], ["Cancel", "Close"]) is None


def test_pick_alert_label_none_on_an_empty_button_list() -> None:
    assert pick_alert_label(["Allow", "OK"], []) is None


def test_pick_alert_label_skips_an_ambiguous_candidate() -> None:
    # A label present twice cannot resolve to one button, so it is skipped rather than tapping
    # whichever matched first (determinism first, mirroring resolve_unique).
    assert pick_alert_label(["OK", "Cancel"], ["OK", "OK", "Cancel"]) == "Cancel"
    assert pick_alert_label(["OK"], ["OK", "OK"]) is None


# --- AlertGuardConfig.probe_native ------------------------------------------------------------------


def test_probe_native_incapable_backend() -> None:
    guard = AlertGuardConfig(vision=_never_vision)
    assert guard.probe_native(_Incapable()) == ("incapable", None)  # type: ignore[arg-type]


def test_probe_native_absent_when_no_alert() -> None:
    guard = AlertGuardConfig(vision=_never_vision)
    assert guard.probe_native(FakeDriver([])) == ("absent", None)  # capable, but no alert seeded


def test_probe_native_dismisses_a_named_button() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event == AlertEvent(label="Allow")
    # It tapped through BE-0316's handle_system_alert with the picked label.
    assert ("handle_system_alert", ({"label": "Allow"}, 0.0)) in driver.actions


def test_probe_native_uses_default_dismissive_labels_when_none_configured() -> None:
    guard = AlertGuardConfig(vision=_never_vision)  # no labels → default dismissive policy
    driver = _fake_with_alert(["Don't Allow", "Allow"])
    state, event = guard.probe_native(driver)
    assert state == "dismissed"
    assert event is not None and event.label == "Don't Allow"
    assert event.label in DEFAULT_DISMISSIVE_LABELS


def test_probe_native_unhandled_when_no_candidate_resolves() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    driver = _fake_with_alert(["Weird Button"])
    assert guard.probe_native(driver) == ("unhandled", None)


def test_probe_native_treats_a_dismiss_race_as_absent() -> None:
    # TOCTOU: the alert vanishes between the presence query and the tap, so handle_system_alert
    # raises ElementNotFound. That is a benign self-resolved race — reported as absent, not a failure.
    class _RaceDriver(FakeDriver):
        def handle_system_alert(self, sel: base.Selector, timeout: float) -> None:
            raise base.ElementNotFound("the alert vanished before the tap")

    driver = _RaceDriver([])
    driver.system_alert_buttons = [_button("Allow")]
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    assert guard.probe_native(driver) == ("absent", None)


# --- AlertGuardConfig.__call__ (native-first, vision fallback) --------------------------------------


def test_call_returns_native_event_without_touching_vision() -> None:
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    assert guard(_fake_with_alert(["Allow"])) == AlertEvent(label="Allow")


def test_call_falls_back_to_vision_when_native_cannot_act() -> None:
    calls = {"n": 0}

    def vision(_driver: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        return AlertEvent(label="vision")

    guard = AlertGuardConfig(vision=vision, labels=["Allow"])
    assert guard(_fake_with_alert(["Weird Button"])) == AlertEvent(
        label="vision"
    )  # unhandled → vision
    assert calls["n"] == 1


def test_call_falls_back_to_vision_on_an_incapable_backend() -> None:
    def vision(_driver: base.Driver) -> AlertEvent | None:
        return AlertEvent(label="vision")

    guard = AlertGuardConfig(vision=vision)
    assert guard(FakeDriver([])) == AlertEvent(label="vision")


# --- the mid-wait gate on the native path -----------------------------------------------------------


def _for_wait(target_id: str, timeout: float):  # type: ignore[no-untyped-def]
    from bajutsu.scenario import Wait

    return Wait.model_validate({"for": {"id": target_id}, "timeout": timeout})


def test_gate_dismisses_natively_mid_wait_and_records_the_alert() -> None:
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"

    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []  # the alert cleared
            d.screen = [target]  # and the awaited element is revealed

    driver = _fake_with_alert(["Allow"], react=react)
    guard = AlertGuardConfig(vision=_never_vision, labels=["Allow"])
    alerts: list[AlertEvent] = []
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert alerts == [AlertEvent(label="Allow")]


def test_gate_absent_native_alert_debounces_a_transient_collapse() -> None:
    # "absent" means the native query saw no *SpringBoard* alert. A single transient collapsed frame
    # under it must not fire the vision path — the debounce filters that false positive (BE-0315 /
    # BE-0269). A *persistent* non-SpringBoard collapse is the separate case the next test covers.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"

    class _OneFrameCollapse(FakeDriver):
        def __init__(self) -> None:
            super().__init__([])  # capable; no alert seeded, so the native probe returns "absent"
            self._polls = 0

        def query(self) -> list[base.Element]:
            self._polls += 1
            return [] if self._polls == 1 else [target]  # one collapsed frame, then app UI

    guard = AlertGuardConfig(vision=_never_vision, poll_interval=0.05)  # probe every condition poll
    ok, reason, _tree = _wait(
        _OneFrameCollapse(), _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert ok and reason == ""  # revealed after one transient collapse; vision never called


def test_gate_absent_native_alert_still_drives_vision_for_a_persistent_collapse() -> None:
    # "absent" only rules out a *SpringBoard* alert: an action sheet or a WKWebView JS dialog the
    # native query cannot enumerate reads as absent too while still collapsing the tree. A persistent
    # collapse under "absent" must still drive the debounced vision fallback mid-wait — restoring the
    # BE-0269 recovery the native path would otherwise skip on the now-default backend (PR #1330).
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    calls = {"n": 0}

    def vision(d: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        d.screen = [target]  # vision clears the non-SpringBoard surface the native query cannot see
        return AlertEvent(label="Dismiss")

    driver = FakeDriver(
        []
    )  # capable, collapsed, no SpringBoard alert seeded → probe returns "absent"
    guard = AlertGuardConfig(vision=vision, poll_interval=0.05)
    alerts: list[AlertEvent] = []
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert calls["n"] == 1  # the vision fallback fired mid-wait for the non-SpringBoard collapse
    assert alerts == [AlertEvent(label="Dismiss")]


def test_gate_polls_the_native_query_on_its_own_interval_not_every_tick() -> None:
    # The native query is rate-limited to one per poll_interval, decoupled from the 50ms condition
    # poll, so it does not roughly double the single-threaded runner's load (BE-0315).
    from bajutsu.orchestrator.waits import _wait

    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def system_alert_labels(self) -> list[str]:
            probes["n"] += 1
            return []  # never an alert, so the wait runs to its full budget

    # App UI is visible (a non-collapsed tree), so the collapsed-tree vision fallback never enters —
    # this isolates the native probe's cadence from the "absent + collapsed" fallback path.
    app_ui = _button("home")
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)
    ok, _reason, _tree = _wait(
        _CountingProbe([app_ui]),
        _for_wait("never", 2.0),
        _LogicalClock(),
        alert_guard=guard,
        alerts=[],
    )
    assert not ok
    # ~40 condition polls over the 2s budget, but the native query fires about once per second:
    # a two-sided bound proves it re-fires on the interval (not just once) yet not every tick.
    assert 2 <= probes["n"] <= 4


def test_gate_unhandled_native_alert_falls_back_to_vision_bounded() -> None:
    # An alert is up but no policy label resolves (unknown button): the gate routes to the vision
    # fallback, bounded by the same attempt ceiling as the collapsed-tree path — never an unbounded
    # per-interval stream of AI-vision calls (BE-0315).
    from bajutsu.orchestrator.waits import _GUARD_MAX_ATTEMPTS, _wait

    calls = {"n": 0}

    def vision(_driver: base.Driver) -> AlertEvent | None:
        calls["n"] += 1
        return None  # vision can't clear it either, so the wait runs to its full budget

    driver = _fake_with_alert(["Weird Button"])  # capable; alert stays up (never dismissed)
    guard = AlertGuardConfig(vision=vision, labels=["Allow"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("never", 30.0), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert calls["n"] == _GUARD_MAX_ATTEMPTS  # bounded, not one call per interval for 30s
