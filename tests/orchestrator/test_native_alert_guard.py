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


class _NonNativeDriver(FakeDriver):
    """A web/Android-shaped backend stand-in: capable of everything `FakeDriver` is, minus
    `HANDLE_SYSTEM_ALERT` — for asserting the in-tree dismiss path stays native-only."""

    def capabilities(self) -> set[str]:
        return super().capabilities() - {base.Capability.HANDLE_SYSTEM_ALERT}


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

    # poll_interval=1.0 rate-limits only the native query; the collapsed-tree debounce still samples
    # every _POLL, so one transient frame that clears before the debounce is filtered regardless.
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)
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
    # poll_interval=1.0 (the realistic default) but a tight 2s budget: the collapsed-tree debounce must
    # sample every _POLL, not once per interval, or vision would first fire at ~3x poll_interval and
    # miss this budget entirely (the regression this locks in). It recovers within a few _POLL ticks.
    guard = AlertGuardConfig(vision=vision, poll_interval=1.0)
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 2.0), clock, alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert (
        clock.now() < 1.0
    )  # recovered at proxy latency, not throttled to the native-probe cadence
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


def test_gate_dismisses_an_app_attached_sheet_from_the_tree_without_vision() -> None:
    # A system-owned prompt that is never SpringBoard-enumerable (e.g. iOS's Save Password sheet,
    # attached to the app's own accessibility tree) reads as "absent" on every native probe forever,
    # yet its own labeled, identifier-less buttons keep `shows_app_ui` from seeing a collapsed tree
    # either — both mid-wait detectors used to sit idle for the whole timeout on exactly this shape.
    # The in-tree label match (BE-0316's gap) must clear it within a poll or two, no vision call.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button(
        "今はしない"
    )  # identifier-less, like a real system-owned sheet's button

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "今はしない", "traits": ["button"]}:
            d.screen = [target]  # dismissing the sheet reveals the awaited element

    driver = FakeDriver([prompt_button], react=react)  # capable; no SpringBoard alert seeded
    guard = AlertGuardConfig(
        vision=_never_vision, labels=["今はしない", "Not Now"], poll_interval=1.0
    )
    alerts: list[AlertEvent] = []
    clock = _LogicalClock()
    ok, reason, _tree = _wait(
        driver, _for_wait("ready", 30.0), clock, alert_guard=guard, alerts=alerts
    )
    assert ok and reason == ""
    assert clock.now() < 1.0  # cleared at tree-poll latency, not anywhere near the 30s timeout
    assert alerts == [AlertEvent(label="今はしない")]


def test_dismiss_from_tree_taps_a_showing_at_most_once() -> None:
    # A real dismiss isn't instant: a fading-out button can linger in the tree for a poll or two
    # after the tap before the screen actually updates. Without its own guard, `_dismiss_from_tree`
    # runs every `_POLL` with no cooldown, so it would re-match and re-tap the same button on every
    # one of those polls — over-counting one dismissal into several `AlertEvent`s and actuating the
    # app repeatedly (a second tap can land on whatever is underneath a fading sheet).
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    class _LingeringDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self._polls_since_tap: int | None = None

        def tap(self, sel: base.Selector) -> None:
            super().tap(sel)
            self._polls_since_tap = 0

        def query(self) -> list[base.Element]:
            if self._polls_since_tap is not None:
                self._polls_since_tap += 1
                if self._polls_since_tap >= 3:  # the animation finishes; the screen updates
                    return [target]
            return list(self.screen)

    driver = _LingeringDismiss()
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 2.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    tap_sel = {"label": "今はしない", "traits": ["button"]}
    assert driver.actions.count(("tap", tap_sel)) == 1  # tapped exactly once
    assert alerts == [AlertEvent(label="今はしない")]  # exactly one dismissal recorded, not several


def test_dismiss_from_tree_declines_on_not_yet_tappable_then_dismisses() -> None:
    # A sheet's own scrim can still cover its button for a poll or two while the presentation
    # animation finishes, the platform's hit-test (`isHittable` / `topmost_at_point`) reading the
    # button as unreachable until it settles. `ElementNotTappable` here is the same benign,
    # self-resolved race `ElementNotFound` and `AmbiguousSelector` already forgive — not a reason
    # to fail the wait.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    class _NotYetTappableDismiss(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            if self.tap_calls < 3:  # the scrim is still animating away
                raise base.ElementNotTappable("covered by the sheet's own scrim")
            super().tap(sel)
            self.screen = [target]  # the animation finishes; the screen updates

    driver = _NotYetTappableDismiss()
    guard = AlertGuardConfig(vision=_never_vision, labels=["今はしない"], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 3.0), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert driver.tap_calls == 3  # declined twice, then dismissed
    assert alerts == [AlertEvent(label="今はしない")]


def test_dismiss_from_tree_declines_on_an_in_app_label_collision() -> None:
    # A system-owned identifier-less button and an app-authored one share a configured label:
    # pick_alert_label resolves uniquely over the identifier-less subset, but the whole-tree tap
    # sees both and must decline rather than tap the wrong one (determinism first). A *persistent*
    # collision (unlike a vanish race) must decline before ever attempting the tap: the collision
    # never clears, so `_tree_dismiss_pending` (only armed on a successful tap) never guards it, and
    # a decline reached only via `except AmbiguousSelector` would re-issue the on-device tap every
    # `_POLL` for the rest of the wait — count `tap()` calls directly, not `driver.actions`, since a
    # raised `AmbiguousSelector` never reaches `_record` either way.
    from bajutsu.orchestrator.waits import _wait

    prompt_button = _button("Not Now")  # identifier-less, system-owned
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"

    class _CountingTapDriver(FakeDriver):
        def __init__(self) -> None:
            super().__init__([prompt_button, app_button])
            self.tap_calls = 0

        def tap(self, sel: base.Selector) -> None:
            self.tap_calls += 1
            super().tap(sel)

    driver = _CountingTapDriver()  # native-capable; no SpringBoard alert
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert driver.actions == []  # ambiguous → declined, nothing tapped
    assert driver.tap_calls == 0  # declined before attempting the tap, not caught after


def test_dismiss_from_tree_dismisses_despite_a_non_button_label_collision() -> None:
    # A bare `{"label": label}` selector resolves via `matches()` (base.py), which matches on
    # `label` alone and ignores `traits` — so a non-button element sharing the exact text (a static
    # caption drawn next to the sheet, a header) would make a trait-unscoped tap ambiguous despite
    # the intended button being uniquely named among buttons. Scoping both the pre-check and the tap
    # itself to `traits: [BUTTON]` (mirroring the `buttons` filter `label` was resolved against)
    # means a same-labeled caption never blocks dismissal at all.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("Not Now")  # identifier-less, system-owned
    caption = _button("Not Now")
    caption["traits"] = ["staticText"]  # not a button; must not block the tap below

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Not Now", "traits": ["button"]}:
            d.screen = [target]

    driver = FakeDriver(
        [prompt_button, caption], react=react
    )  # native-capable; no SpringBoard alert
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
    alerts: list[AlertEvent] = []
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=alerts
    )
    assert ok
    assert alerts == [AlertEvent(label="Not Now")]


def test_dismiss_from_tree_never_matches_an_in_app_button_carrying_an_identifier() -> None:
    # An app screen with its own button that happens to share a policy label (e.g. a real in-app
    # "Not Now") must never be tapped by the guard — only a system-owned, identifier-less button can
    # match, same restriction `system_alert_labels()` already assumes for a genuine SpringBoard alert.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    app_button = _button("Not Now")
    app_button["identifier"] = "screen.home.button.not-now"  # an app-authored button, not a prompt

    driver = FakeDriver([app_button])  # capable; no SpringBoard alert seeded; never reveals "ready"
    guard = AlertGuardConfig(vision=_never_vision, labels=["Not Now"], poll_interval=1.0)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # the in-app button was never tapped, so "ready" never appears
    assert driver.actions == []  # confirms no tap was issued against it


def test_dismiss_from_tree_never_fires_on_a_non_native_backend() -> None:
    # The in-tree match is native-only (the app-attached-sheet case it targets is an iOS one): a
    # web/Android-shaped backend must keep its pre-existing behavior untouched — only the
    # collapsed-tree + vision path (BE-0269) can act there, never the fast in-tree tap.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    prompt_button = _button("今はしない")

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "今はしない"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = _NonNativeDriver([prompt_button], react=react)
    guard = AlertGuardConfig(
        vision=_never_vision, labels=["今はしない", "Not Now"], poll_interval=1.0
    )
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok  # never tapped, so "ready" never appears; the wait runs to its full budget
    assert driver.actions == []


def test_dismiss_from_tree_never_fires_on_default_dismissive_labels_alone() -> None:
    # Without an explicit `systemAlertHandling.instruction`, only `DEFAULT_DISMISSIVE_LABELS`
    # applies — generic English UI vocabulary ("Cancel", "Close", …) a real app screen can
    # legitimately show. The fast in-tree path must stay off unless the scenario author opted in
    # with their own `labels`; a default-only guard falls back to the collapsed-tree + vision path,
    # same as before this path existed.
    from bajutsu.orchestrator.waits import _wait

    target = _button("R")
    target["identifier"] = "ready"
    cancel_button = _button("Cancel")  # identifier-less; matches DEFAULT_DISMISSIVE_LABELS

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"label": "Cancel"}:
            d.screen = [target]  # would reveal "ready" if the in-tree path fired (it must not)

    driver = FakeDriver([cancel_button], react=react)  # native-capable; no labels configured
    guard = AlertGuardConfig(vision=_never_vision, poll_interval=1.0)  # labels=[] (default)
    ok, _reason, _tree = _wait(
        driver, _for_wait("ready", 0.2), _LogicalClock(), alert_guard=guard, alerts=[]
    )
    assert not ok
    assert driver.actions == []


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
