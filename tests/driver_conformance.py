"""The driver conformance contract (BE-0114): one spec, every backend.

Driver tests were written per backend, so the determinism-core invariants each backend must
uphold were asserted separately (or not at all) for each. This module states those invariants
once, as an executable contract, and runs the *same* test body against every backend — a TCK
(technology compatibility kit) for the `Driver` Protocol.

The contract is the definition a new backend meets: implement `ConformanceHarness` for the
backend, subclass `DriverConformanceContract`, and pytest collects the inherited test bodies
against it. `tests/test_driver_conformance.py` does this for `FakeDriver` on the fast Linux
gate; the Playwright (web CI) and XCUITest (on-device E2E) harnesses plug into the same
contract without a second spec.

The invariants (grounded in the `Driver` Protocol and `drivers/base`):

* An ambiguous selector (2+ matches) fails rather than acting on the first match.
* A zero-match selector fails rather than reporting success.
* Selector failures share one error type (`SelectorError`), uniform across backends.
* A unique match acts without error.
* `capabilities()` matches observed behavior — the `QUERY` / `ELEMENTS` baseline is declared,
  multi-touch gestures work exactly when `MULTI_TOUCH` is declared (else raise loudly),
  select-all / clipboard copy work exactly when `TEXT_SELECTION` is declared (else raise loudly),
  and a picker wheel is set exactly when `PICKER_WHEEL` is declared (else raise loudly).
* Text editing round-trips on the focused field: typing then deleting reduces the field's
  reported length, on every backend that surfaces the field's value.
* `tap_point` (a raw coordinate tap) focuses the field when aimed at its center — the same
  observable effect as a semantic tap on it — so the alert-dismissal coordinate tap has a
  contract, not just a per-backend command test.
* `wait_for` is a single-shot check of the current screen; the shared `wait_until` loop turns
  it into a condition wait with no fixed sleep.
* A read taken after a content-moving gesture reflects the moved screen, not the tree from before it
  (`the marked-read contract`, BE-0332): the after-scroll read differs from the before-scroll one.

The text-editing and `tap_point` invariants need a real editable field on the screen, so every
conformance screen carries one (`FIELD_ID`) alongside the readiness marker — always present, like
the marker, not seeded per screen. It has a known, queryable frame, so it doubles as the
known-frame element the coordinate-tap invariant aims at.

This module is not collected by pytest itself (no ``test_`` filename, no ``Test`` class name);
it is imported by the per-backend suites.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Callable
from typing import Protocol, runtime_checkable

import pytest

from bajutsu.drivers import base
from bajutsu.orchestrator.actions.handlers.scroll import (
    _center_in_viewport,
    _resolve_target,
    _viewport,
    scroll_to_target,
)

#: The scroll conformance screen (BE-0326): a scrollable list whose later rows start below the fold.
#: Each backend realizes the same fixed layout — a marker, `SCROLL_ROW_COUNT` rows, and a row taller
#: than the viewport — so the `scroll` action's re-query loop is driven against every real driver's
#: query / scroll / viewport code. The sentinel id is what a harness seeds to ask for this screen
#: (the app renders the fixed layout for it rather than a button carrying that id).
SCROLL_SENTINEL = "conformance.scroll"
SCROLL_ROW_PREFIX = "conformance.scroll.row."
SCROLL_ROW_COUNT = 20
SCROLL_TALL_ID = "conformance.scroll.tall"
SCROLL_FIRST_ROW = f"{SCROLL_ROW_PREFIX}0"
SCROLL_LAST_ROW = f"{SCROLL_ROW_PREFIX}{SCROLL_ROW_COUNT - 1}"

#: A generous scroll bound for the conformance screen — far more steps than reaching the bottom needs.
SCROLL_MAX = 30

#: The editable text field present on every conformance screen (BE-0280), alongside the readiness
#: marker. The text-editing and `tap_point` invariants act on it; each backend's screen realizes it
#: (the iOS `ConformanceView`, the Compose `ConformanceScreen`, the web `_render`, and — for the
#: fast gate — the reactive `FakeDriver` the `FakeConformanceHarness` builds). Its `query()` `value`
#: reflects what has been typed, so the round-trip effect is observable, and its frame is known, so
#: the coordinate tap has a definite center to aim at.
FIELD_ID = "conformance.field"

#: The masked input present on every conformance screen (BE-0331), beside the plain one above. Its
#: whole purpose is that `query()` reports `Trait.SECURE_TEXT_FIELD` for it on every backend: the
#: default that masks such a field's value without configuration is only as portable as the trait
#: underneath it, so a backend that stops deriving the trait from its own source (XCUITest's
#: `secureTextField` type, the web `input[type=password]`, the Android node's `password` flag) fails
#: the suite here rather than silently dropping the masking on that platform alone.
SECURE_FIELD_ID = "conformance.secureField"

#: The obstruction conformance screen: a target genuinely covered by another element at the same
#: point, plus an unobstructed control on the same screen so `is_tappable` is exercised both ways
#: in one place. `obstruction_screen` is optional on `ConformanceHarness` — a harness that cannot
#: yet realize genuine on-screen overlap simply omits it, and the tests that need it skip rather
#: than fail (the same tolerance `with_screen`'s docstring already grants chrome/container elements).
OBSTRUCTION_TARGET_ID = "conformance.obstruction.target"
OBSTRUCTION_COVER_ID = "conformance.obstruction.cover"
OBSTRUCTION_CLEAR_ID = "conformance.obstruction.clear"

#: Two always-present, independently-mirrored tap targets (BE-0339 Unit 6), alongside the field and
#: the marker. Each is a *pair* of elements — a static tap target plus a separate element mirroring
#: its tap count in `value`, starting at "0" — the same split `LogScreen.kt`'s `log.longpress` /
#: `log.longpress.value` and `log.doubletap` / `log.doubletap.value` use, and not a coincidence: the
#: tap target's own identity must stay stable across the tap that resolves and injects it, which a
#: `value` that changes as a *result* of that same tap cannot guarantee. Two pairs, not one, so a tap
#: that lands on the wrong element (a coordinate neighbor a stale resolve happened to hit, the exact
#: shape of the flake this item closes) is observable: tapping A must move only A's count, never B's.
TAP_MIRROR_A_ID = "conformance.tapMirror.a"
TAP_MIRROR_A_VALUE_ID = "conformance.tapMirror.a.value"
TAP_MIRROR_B_ID = "conformance.tapMirror.b"
TAP_MIRROR_B_VALUE_ID = "conformance.tapMirror.b.value"


def field_value(driver: base.Driver) -> str:
    """The current text of the conformance field (empty string when it reports none)."""
    return base.resolve_unique(driver.query(), {"id": FIELD_ID})["value"] or ""


def _mirror_value(driver: base.Driver, identifier: str) -> str:
    """The current tap count mirrored at `identifier` (`TAP_MIRROR_A_VALUE_ID` / `_B_VALUE_ID`)."""
    return base.resolve_unique(driver.query(), {"id": identifier})["value"] or "0"


def _field_center(driver: base.Driver) -> base.Point:
    """The center point of the conformance field's known frame, for a coordinate tap."""
    # Route the arithmetic through the shared helper the backends use (BE-0251), not a second copy.
    return base.frame_center(base.resolve_unique(driver.query(), {"id": FIELD_ID})["frame"])


def _rows_in_viewport(driver: base.Driver) -> frozenset[int]:
    """The scroll-row indices whose frame center is currently on-screen, as one read sees them.

    A backend-neutral projection of "which screen the read describes": a native lazy list drops
    off-screen rows from the tree, a retained tree keeps them with out-of-viewport centers, so both
    reduce to the same set of on-screen rows. Comparing this before and after a scroll tells whether
    a read reflects the moved screen or the one from before it.
    """
    els = driver.query()
    viewport = _viewport(driver, els)
    return frozenset(
        i
        for i in range(SCROLL_ROW_COUNT)
        if (row := _resolve_target(els, {"id": f"{SCROLL_ROW_PREFIX}{i}"})) is not None
        and _center_in_viewport(row["frame"], viewport)
    )


def element(
    *,
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: base.Frame = (0.0, 0.0, 10.0, 10.0),
    native_z: float | None = None,
) -> base.Element:
    """Build one `Element` for a conformance screen — a plain fixture, not a behavior mock."""
    return base.Element(
        identifier=identifier,
        label=label,
        traits=traits if traits is not None else [],
        value=value,
        frame=frame,
        nativeZ=native_z,
    )


@runtime_checkable
class ConformanceHarness(Protocol):
    """A backend's adapter to the contract: hand it a screen, get a driver showing it.

    Each backend realizes a requested screen its own way — `FakeDriver` takes the elements
    directly, a browser renders them, an app presents them — so the contract can drive the real
    driver instance (including any code that bypasses `drivers/base`), not the shared base alone.

    `obstruction_screen(self) -> base.Driver` is a genuinely optional structural extra, deliberately
    left off this `Protocol`'s declared surface rather than given a docstring-only body here: a
    harness whose backend cannot yet realize genuine on-screen overlap simply has no such method,
    and the contract tests that need it check for its presence with `hasattr` and skip, rather than
    fail, when it is absent (the same tolerance `test_delete_text_reduces_the_field_length` already
    grants a backend that cannot surface a field's value). Declaring it here instead — even as a
    docstring-only body — would make it an inherited, concrete method on any harness written as
    `class MyHarness(ConformanceHarness)`, so `hasattr` would read `True` and the skip would never
    fire, even though calling it would just return `None`. When a harness does implement it, it
    should return a driver showing `OBSTRUCTION_TARGET_ID` genuinely covered by
    `OBSTRUCTION_COVER_ID` at the same point, plus an unobstructed `OBSTRUCTION_CLEAR_ID` on the
    same screen.
    """

    backend: str

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        """Return a driver whose `query()` reports at least `elements`.

        A real backend may also surface chrome or container elements it was not asked to
        seed (a browser document, an app's navigation bar), so the contract requires the
        seeded elements to be present, not that the screen equals them exactly.
        """

    def scrollable_screen(self) -> base.Driver:
        """Return a driver showing the tall, vertically scrollable screen (BE-0326).

        The screen stacks `SCROLL_ROW_COUNT` rows (`SCROLL_ROW_PREFIX{i}`) and a row taller than the
        viewport (`SCROLL_TALL_ID`) in a container taller than the viewport, so the later rows and the
        tall row start below the fold. A backend that keeps off-screen nodes in its tree (web) or
        models a viewport (fake) reports them off-screen; a native lazy list drops them until scrolled
        to — the `scroll` action's re-query loop reveals the target on either.
        """


class OnDeviceConformanceHarness:
    """Shared base for the on-device harnesses: realize a seeded screen, then wait until it renders.

    The two on-device backends realize a screen differently — the iOS harness writes a spec file the
    app polls, the Android harness re-launches the activity with a new intent extra — but once seeded,
    both wait the same way: poll `query()` until the readiness marker is present, every seeded id is
    present at its full multiplicity, and every dropped id is gone. That condition-backed wait (no
    fixed sleep) is correctness-sensitive — the multiplicity guard is what makes the ambiguous "two
    `dup`s" case real, the gone-set what makes the empty (zero-match) screen real — so it lives here
    once rather than being copied per backend, where it could silently drift and weaken the contract
    on one actuator only. A backend supplies just `_realize(ids)`: how it pushes the spec to the app.

    Carries no `Test` prefix, so pytest never collects it; the per-backend suites subclass it.
    """

    #: Present on every conformance screen, the empty (zero-match) one included, so readiness is a
    #: positive check "conformance mode is active" rather than an inference from an absent tree (which
    #: a transient near-empty tree during a relaunch could meet too early). Mirrors the app-side marker
    #: (iOS `ConformanceView.readyID`, Compose `ConformanceScreen.CONFORMANCE_READY_ID`).
    READY_ID = "conformance.ready"

    def __init__(self, backend: str, driver: base.Driver) -> None:
        self.backend = backend
        self._driver = driver
        self._prev: list[str] = []

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        ids = [el["identifier"] for el in elements if el["identifier"] is not None]
        self._realize(ids)
        # Ids the previous screen had that this one drops must be gone before we proceed — the marker
        # is always present, so without this the empty (zero-match) screen would "be ready" while the
        # last screen's ids still linger (the app updates ~asynchronously after `_realize`).
        self._await_screen(ids, gone=set(self._prev) - set(ids))
        self._prev = ids
        return self._driver

    def scrollable_screen(self) -> base.Driver:
        # Realize the empty screen first so the app tears down any list a prior test left scrolled:
        # re-seeding the same sentinel would leave that scroll offset in place, and `row.0` might never
        # return. Wait until every scroll row is gone (the list unmounted), then seed the sentinel and
        # wait for a fresh `row.0` at the top. The app renders the fixed scroll layout for the sentinel
        # (a lazy list, so the later rows drop from the a11y tree until scrolled to); the sentinel is
        # not itself a rendered element, so the marker plus the first row is the readiness signal.
        all_rows = {f"{SCROLL_ROW_PREFIX}{i}" for i in range(SCROLL_ROW_COUNT)} | {SCROLL_TALL_ID}
        self._realize([])
        self._await_screen([], gone=all_rows)
        self._realize([SCROLL_SENTINEL])
        self._await_screen([SCROLL_FIRST_ROW], gone=set())
        self._prev = [SCROLL_FIRST_ROW]
        return self._driver

    def _realize(self, ids: list[str]) -> None:
        """Push the seeded identifier set to the app so it re-renders (backend-specific)."""
        raise NotImplementedError

    def _await_screen(
        self, ids: list[str], gone: set[str], timeout: float = 30.0, poll: float = 0.1
    ) -> None:
        # Condition-backed (no fixed sleep): the app re-renders asynchronously after `_realize`, so
        # wait on the observed screen, not a guessed delay. Ready = the conformance-mode marker
        # present, every seeded id present at its full multiplicity, and every dropped id gone.
        # Multiplicity matters for the ambiguous case (two `dup`s): set membership could proceed with
        # only one rendered, so the contract would see a unique match. None identifiers are ignored.
        want = Counter(ids)
        deadline = time.monotonic() + timeout
        while True:
            have = Counter(el["identifier"] for el in self._driver.query() if el["identifier"])
            present = have[self.READY_ID] and all(have[i] >= n for i, n in want.items())
            if present and not any(g in have for g in gone):
                return
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"conformance screen not ready: want {ids}, gone {sorted(gone)}, saw {sorted(have)}"
                )
            time.sleep(poll)


class DriverConformanceContract:
    """The backend-agnostic contract every `Driver` must satisfy.

    Subclass per backend with a `harness` fixture returning a `ConformanceHarness`; pytest
    collects these inherited test bodies against it. The base class carries no `Test` prefix,
    so pytest never collects it on its own.
    """

    def test_ambiguous_selector_fails_rather_than_acting(self, harness: ConformanceHarness) -> None:
        # Two matches, no way to disambiguate: a single action must fail, not tap the first. Distinct
        # frames make the two genuinely separate elements — a content-identical duplicate (same
        # identifier/label/traits/value/frame) is a different case that `resolve_unique` now collapses
        # instead of flagging ambiguous.
        driver = harness.with_screen(
            [
                element(identifier="dup", frame=(0.0, 0.0, 10.0, 10.0)),
                element(identifier="dup", frame=(0.0, 20.0, 10.0, 10.0)),
            ]
        )
        with pytest.raises(base.AmbiguousSelector):
            driver.tap({"id": "dup"})

    def test_zero_match_fails_rather_than_succeeding(self, harness: ConformanceHarness) -> None:
        driver = harness.with_screen([])
        with pytest.raises(base.ElementNotFound):
            driver.tap({"id": "missing"})

    def test_selector_failures_share_one_error_type(self, harness: ConformanceHarness) -> None:
        # Both failure modes are SelectorError, so a caller catches them uniformly on any backend.
        # Distinct frames, as above: two genuinely separate elements, not a collapsible duplicate.
        ambiguous = harness.with_screen(
            [
                element(identifier="dup", frame=(0.0, 0.0, 10.0, 10.0)),
                element(identifier="dup", frame=(0.0, 20.0, 10.0, 10.0)),
            ]
        )
        with pytest.raises(base.SelectorError):
            ambiguous.tap({"id": "dup"})
        empty = harness.with_screen([])
        with pytest.raises(base.SelectorError):
            empty.tap({"id": "missing"})

    def test_unique_match_acts_without_error(self, harness: ConformanceHarness) -> None:
        driver = harness.with_screen([element(identifier="ok")])
        driver.tap({"id": "ok"})

    def test_label_and_trait_selector_resolves_a_button(self, harness: ConformanceHarness) -> None:
        # A tab bar is reached cross-backend by `{ label, traits: [button] }` (BE-0107 / BE-0223):
        # the trait narrows the label to the tappable control, so a scenario authored once switches
        # tabs on every backend. This pins that resolution path as a contract invariant, not merely
        # an emergent property of the showcase lane. The seed's identifier equals its label so the
        # on-device harness — which renders each seeded id as a labelled button — realizes it too.
        driver = harness.with_screen(
            [element(identifier="Log", label="Log", traits=[base.Trait.BUTTON])]
        )
        driver.tap({"label": "Log", "traits": [base.Trait.BUTTON]})

    def test_query_reports_the_seeded_screen(self, harness: ConformanceHarness) -> None:
        driver = harness.with_screen([element(identifier="a"), element(identifier="b")])
        identifiers = {el["identifier"] for el in driver.query()}
        assert {"a", "b"} <= identifiers

    def test_a_masked_input_reports_the_secure_trait(self, harness: ConformanceHarness) -> None:
        # Redaction masks such a field's value with no configuration (BE-0331), so the default is
        # portable only if every backend derives the trait from its own platform source. Asserting it
        # on `query()` output is what makes "the platform marked this field secret" a contract rather
        # than an iOS-only accident — the plain field beside it must *not* carry the trait, or the
        # default would mask ordinary text everywhere.
        driver = harness.with_screen([])
        elements = driver.query()
        secure = base.resolve_unique(elements, {"id": SECURE_FIELD_ID})
        assert base.Trait.SECURE_TEXT_FIELD in secure["traits"]
        plain = base.resolve_unique(elements, {"id": FIELD_ID})
        assert base.Trait.SECURE_TEXT_FIELD not in plain["traits"]

    def test_native_z_is_absent_or_a_real_measurement(self, harness: ConformanceHarness) -> None:
        # `nativeZ` (BE-0355) is diagnostic only and app-measured: every element carries the field,
        # and a backend reports a number only where the app under test measured one for that element.
        # Everything else is `None` — an honest absence over a value derived from the tree's own
        # document order, which is the wrong-but-authoritative reading this field exists to avoid.
        # Both halves are asserted here because a backend can be on either side of the opt-in: the
        # fake and any app that never links the app-side hook report the absence, while an
        # instrumented app reports positions this pins to real, finite numbers.
        elements = harness.with_screen([element(identifier="a"), element(identifier="b")]).query()
        assert all("nativeZ" in el for el in elements)
        measured = [el["nativeZ"] for el in elements if el["nativeZ"] is not None]
        assert all(isinstance(z, float) and math.isfinite(z) for z in measured)

    def test_baseline_capabilities_are_declared(self, harness: ConformanceHarness) -> None:
        # Every backend must read the screen: the preflight baseline (BE-0082) is QUERY + ELEMENTS.
        driver = harness.with_screen([element(identifier="a")])
        caps = driver.capabilities()
        assert base.Capability.QUERY in caps
        assert base.Capability.ELEMENTS in caps

    def test_multi_touch_capability_matches_behavior(self, harness: ConformanceHarness) -> None:
        # capabilities() is a promise: a MULTI_TOUCH backend performs pinch/rotate, a single-touch
        # one refuses loudly (UnsupportedAction) instead of silently no-op'ing.
        driver = harness.with_screen([element(identifier="g")])
        supports_multi_touch = base.Capability.MULTI_TOUCH in driver.capabilities()
        gestures: tuple[Callable[[], None], ...] = (
            lambda: driver.pinch({"id": "g"}, 2.0),
            lambda: driver.rotate({"id": "g"}, 1.0),
        )
        for gesture in gestures:
            if supports_multi_touch:
                gesture()  # must not raise UnsupportedAction
            else:
                with pytest.raises(base.UnsupportedAction):
                    gesture()

    def test_select_option_capability_matches_behavior(self, harness: ConformanceHarness) -> None:
        # capabilities() is a promise: a SELECT_OPTION backend must not raise UnsupportedAction for
        # select_option (though it may raise SelectorError / other errors — e.g. Playwright's harness
        # renders <div> not <select>, so ElementNotFound is expected and acceptable); a non-supporting
        # backend must raise UnsupportedAction rather than silently no-op'ing (same shape as MULTI_TOUCH).
        driver = harness.with_screen([element(identifier="sel")])
        supports = base.Capability.SELECT_OPTION in driver.capabilities()
        if supports:
            try:
                driver.select_option({"id": "sel"}, "opt")
            except base.UnsupportedAction:
                pytest.fail(
                    "SELECT_OPTION capability declared but select_option raised UnsupportedAction"
                )
            except Exception:
                pass  # SelectorError / ElementNotFound / etc. are acceptable for a non-<select> element
        else:
            with pytest.raises(base.UnsupportedAction):
                driver.select_option({"id": "sel"}, "opt")

    def test_picker_wheel_capability_matches_behavior(self, harness: ConformanceHarness) -> None:
        # capabilities() is a promise (BE-0356): a PICKER_WHEEL backend must not raise
        # UnsupportedAction for set_picker_value; one without it must raise rather than silently
        # no-op'ing (the same shape as MULTI_TOUCH / SELECT_OPTION). A supporting backend may still
        # fail some other way — the seeded element is an ordinary one, not a real wheel, and no
        # harness can seed a wheel's rows — so any non-UnsupportedAction error is acceptable, exactly
        # the tolerance `test_select_option_capability_matches_behavior` already grants a non-<select>.
        driver = harness.with_screen([element(identifier="wheel", traits=["pickerWheel"])])
        supports = base.Capability.PICKER_WHEEL in driver.capabilities()
        if supports:
            try:
                driver.set_picker_value({"id": "wheel"}, "opt")
            except base.UnsupportedAction:
                pytest.fail(
                    "PICKER_WHEEL capability declared but set_picker_value raised UnsupportedAction"
                )
            except Exception:
                pass  # not a real wheel / no seeded rows: any other failure is acceptable here
        else:
            with pytest.raises(base.UnsupportedAction):
                driver.set_picker_value({"id": "wheel"}, "opt")

    def test_text_selection_capability_matches_behavior(self, harness: ConformanceHarness) -> None:
        # capabilities() is a promise (BE-0280): a TEXT_SELECTION backend actuates select-all + copy
        # without UnsupportedAction; one without it (a coordinate-only backend) refuses both loudly rather
        # than silently no-op'ing — the same shape as MULTI_TOUCH. `delete_text` / `type_text` are
        # never gated (every backend backs them), so they must succeed on either side of the branch.
        driver = harness.with_screen([])  # the field is always present; no seeded buttons needed
        driver.tap({"id": FIELD_ID})  # focus the field, as the orchestrator does before editing
        driver.type_text("abc")
        driver.delete_text(1)  # actuates everywhere, capability-independent
        if base.Capability.TEXT_SELECTION in driver.capabilities():
            driver.select_all()  # must not raise
            driver.copy_selection()  # must not raise
        else:
            with pytest.raises(base.UnsupportedAction):
                driver.select_all()
            with pytest.raises(base.UnsupportedAction):
                driver.copy_selection()

    def test_delete_text_reduces_the_field_length(self, harness: ConformanceHarness) -> None:
        # The round-trip observable effect (BE-0280): typing grows the field, deleting shrinks it.
        # Measured as deltas, not absolutes, so a value left by an earlier test doesn't matter. A
        # backend that focuses and types but never surfaces the field's `value` can't observe the
        # effect — skip there rather than fail, the same tolerance select_option gives a non-<select>.
        driver = harness.with_screen([])
        driver.tap({"id": FIELD_ID})
        before = len(field_value(driver))
        driver.type_text("wxyz")
        typed = len(field_value(driver))
        if typed <= before:
            pytest.skip("backend does not surface the field value; delete effect not observable")
        driver.delete_text(2)
        assert len(field_value(driver)) < typed

    def test_tap_point_focuses_the_field_like_a_semantic_tap(
        self, harness: ConformanceHarness
    ) -> None:
        # tap_point is a raw coordinate tap (the alert-dismissal path, BE-0269); no lane actuated it
        # under the contract before (BE-0280). Aimed at the field's center, it must focus the field —
        # the same observable effect as a semantic tap on it: typing afterward lands in the field.
        driver = harness.with_screen([element(identifier="elsewhere")])
        # First confirm the field surfaces typed text through the known-good semantic tap, so a
        # backend that never reports `value` is skipped here rather than at the coordinate-tap
        # assertion — otherwise a genuinely broken tap_point (field left unfocused, length unchanged
        # from an empty start) would look identical to "value not observable" and be masked.
        driver.tap({"id": FIELD_ID})
        driver.type_text("a")
        baseline = len(field_value(driver))
        if baseline == 0:
            pytest.skip("backend does not surface the field value; focus effect not observable")
        # Blur by tapping elsewhere, then re-focus by a raw coordinate tap at the field's center: the
        # coordinate tap must land on the field, so the following character grows it. A tap_point that
        # missed would leave the field unfocused and the length unchanged — a failure, not a skip.
        driver.tap({"id": "elsewhere"})
        driver.tap_point(_field_center(driver))
        driver.type_text("z")
        assert len(field_value(driver)) > baseline

    def test_wait_for_is_single_shot(self, harness: ConformanceHarness) -> None:
        # wait_for reflects the current screen only; the deadline loop lives in wait_until.
        present = harness.with_screen([element(identifier="s")])
        assert present.wait_for({"id": "s"}) is True
        absent = harness.with_screen([])
        assert absent.wait_for({"id": "s"}) is False

    def test_wait_until_is_condition_backed(self, harness: ConformanceHarness) -> None:
        # The shared loop resolves on the condition, not a fixed sleep: poll=0 returns at once.
        present = harness.with_screen([element(identifier="s")])
        assert base.wait_until(present, {"id": "s"}, timeout=0, poll=0) is True
        absent = harness.with_screen([])
        assert base.wait_until(absent, {"id": "s"}, timeout=0, poll=0) is False

    def test_scroll_reveals_an_offscreen_target(self, harness: ConformanceHarness) -> None:
        # The core cross-backend contract (BE-0326): a target below the fold starts off-screen (absent
        # from a lazy tree, or present with an out-of-viewport center), and `scroll` reveals it —
        # `scroll()` is non-inertial and `query()` reports the target on-screen, on every backend.
        driver = harness.scrollable_screen()
        target: base.Selector = {"id": SCROLL_LAST_ROW}
        viewport = _viewport(driver, driver.query())
        initial = _resolve_target(driver.query(), target)
        assert initial is None or not _center_in_viewport(initial["frame"], viewport)
        scroll_to_target(driver, target, "down", None, SCROLL_MAX)
        revealed = base.resolve_unique(driver.query(), target)
        assert _center_in_viewport(revealed["frame"], _viewport(driver, driver.query()))

    def test_scroll_reveals_a_target_taller_than_the_viewport(
        self, harness: ConformanceHarness
    ) -> None:
        # A target taller than the viewport still resolves once its *center* is on-screen — the reason
        # the stop condition checks the center, not whole-frame containment (BE-0326). This needs an
        # exact viewport (a tall frame's edges straddle the screen), which every backend now reports.
        driver = harness.scrollable_screen()
        target: base.Selector = {"id": SCROLL_TALL_ID}
        scroll_to_target(driver, target, "down", None, SCROLL_MAX)
        revealed = base.resolve_unique(driver.query(), target)
        assert _center_in_viewport(revealed["frame"], _viewport(driver, driver.query()))

    def test_scroll_fails_when_the_target_is_absent(self, harness: ConformanceHarness) -> None:
        # A target no row carries fails deterministically — the region bottoms out (a scroll stops
        # changing it) or the bound is spent — rather than scrolling forever (BE-0326).
        driver = harness.scrollable_screen()
        with pytest.raises(base.ElementNotFound):
            scroll_to_target(driver, {"id": "conformance.scroll.absent"}, "down", None, SCROLL_MAX)

    def test_a_read_postdates_a_content_moving_gesture(self, harness: ConformanceHarness) -> None:
        # The marked-read contract (BE-0332), observed: a read taken after a gesture that moves the
        # content whole reflects the moved screen, never the tree from before the gesture. On Android
        # the resident reader stamps each read with the device-clock time of the newest accessibility
        # event, and the driver marks the device clock before the gesture, so a settle trusts a read
        # only once its mark postdates the actuation — the ordering that keeps a late tree (the same
        # one `scroll` mistakes for the end of content) from settling a poll on a pre-gesture screen.
        # Asserted as the observable ordering — the read after the scroll differs from the read before
        # it — rather than the driver's internal early-release flag, which is a best-effort signal set
        # only when the mark path fires and is legitimately false when the barrier closes on its
        # budget (a bottomed-out final scroll publishes no event to postdate). A synchronous backend
        # reads the moved screen at once, so the ordering holds there too.
        driver = harness.scrollable_screen()
        before = _rows_in_viewport(driver)
        # A scroll to a row below the fold moves frames wholesale from the top — the exact case a late
        # tree describes from before the gesture — and drives the driver's catch-up barrier.
        scroll_to_target(driver, {"id": SCROLL_LAST_ROW}, "down", None, SCROLL_MAX)
        after = _rows_in_viewport(driver)
        assert after != before

    def test_a_tap_lands_on_the_element_the_selector_named(
        self, harness: ConformanceHarness
    ) -> None:
        # BE-0339 Unit 6: the contract the fast gate cannot otherwise reach — after a gesture, the
        # element that reacted is the element the selector named, never a coordinate neighbor a stale
        # resolve happened to land on (the Motivation section's failure mode: a swipe anchored on a
        # pre-gesture frame, or a tap injected a round trip after the coordinate was computed). Two
        # independently-mirrored targets make a wrong-neighbor tap observable in either direction.
        driver = harness.with_screen([])
        a_before = int(_mirror_value(driver, TAP_MIRROR_A_VALUE_ID))
        b_before = int(_mirror_value(driver, TAP_MIRROR_B_VALUE_ID))
        driver.tap({"id": TAP_MIRROR_A_ID})
        # A bounded condition wait, not one read (no fixed sleep): the accessibility update follows
        # the gesture, and a bare `query()` waits out no barrier — on the dump path it carries no
        # device mark either — so a lone read here can still describe the pre-tap tree and fail a
        # correct tap, exactly the publish lag this item exists to close. Waiting on the exact
        # successor count also rejects a gesture that double-fired.
        assert base.wait_until(
            driver,
            {"id": TAP_MIRROR_A_VALUE_ID, "value": str(a_before + 1)},
            timeout=5.0,
            poll=0.1,
        )
        assert int(_mirror_value(driver, TAP_MIRROR_B_VALUE_ID)) == b_before
        driver.tap({"id": TAP_MIRROR_B_ID})
        assert base.wait_until(
            driver,
            {"id": TAP_MIRROR_B_VALUE_ID, "value": str(b_before + 1)},
            timeout=5.0,
            poll=0.1,
        )
        assert int(_mirror_value(driver, TAP_MIRROR_A_VALUE_ID)) == a_before + 1

    def test_is_tappable_reflects_real_on_screen_occlusion(
        self, harness: ConformanceHarness
    ) -> None:
        # is_tappable is false for a target genuinely covered by another element at its own point,
        # and true for an unobstructed one on the same screen — realized the idiomatic way per
        # backend (native isHittable, a real elementFromPoint hit-test, or the document-order proxy),
        # not merely asserted against the shared base's own geometry helper.
        if not hasattr(harness, "obstruction_screen"):
            pytest.skip("harness does not yet realize a genuine on-screen occlusion")
        driver = harness.obstruction_screen()
        assert driver.is_tappable({"id": OBSTRUCTION_TARGET_ID}) is False
        assert driver.is_tappable({"id": OBSTRUCTION_CLEAR_ID}) is True

    def test_tap_raises_element_not_tappable_when_covered(
        self, harness: ConformanceHarness
    ) -> None:
        if not hasattr(harness, "obstruction_screen"):
            pytest.skip("harness does not yet realize a genuine on-screen occlusion")
        driver = harness.obstruction_screen()
        with pytest.raises(base.ElementNotTappable):
            driver.tap({"id": OBSTRUCTION_TARGET_ID})
        driver.tap({"id": OBSTRUCTION_CLEAR_ID})  # unobstructed — must not raise
