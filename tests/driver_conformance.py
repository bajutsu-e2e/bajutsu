"""The driver conformance contract (BE-0114): one spec, every backend.

Driver tests were written per backend, so the determinism-core invariants each backend must
uphold were asserted separately (or not at all) for each. This module states those invariants
once, as an executable contract, and runs the *same* test body against every backend — a TCK
(technology compatibility kit) for the `Driver` Protocol.

The contract is the definition a new backend meets: implement `ConformanceHarness` for the
backend, subclass `DriverConformanceContract`, and pytest collects the inherited test bodies
against it. `tests/test_driver_conformance.py` does this for `FakeDriver` on the fast Linux
gate; the Playwright (web CI) and idb / XCUITest (on-device E2E) harnesses plug into the same
contract without a second spec.

The invariants (grounded in the `Driver` Protocol and `drivers/base`):

* An ambiguous selector (2+ matches) fails rather than acting on the first match.
* A zero-match selector fails rather than reporting success.
* Selector failures share one error type (`SelectorError`), uniform across backends.
* A unique match acts without error.
* `capabilities()` matches observed behavior — the `QUERY` / `ELEMENTS` baseline is declared,
  and multi-touch gestures work exactly when `MULTI_TOUCH` is declared (else raise loudly).
* `wait_for` is a single-shot check of the current screen; the shared `wait_until` loop turns
  it into a condition wait with no fixed sleep.

This module is not collected by pytest itself (no ``test_`` filename, no ``Test`` class name);
it is imported by the per-backend suites.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Protocol, runtime_checkable

import pytest

from bajutsu.drivers import base


def element(
    *,
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
    frame: base.Frame = (0.0, 0.0, 10.0, 10.0),
) -> base.Element:
    """Build one `Element` for a conformance screen — a plain fixture, not a behavior mock."""
    return base.Element(
        identifier=identifier,
        label=label,
        traits=traits if traits is not None else [],
        value=value,
        frame=frame,
    )


@runtime_checkable
class ConformanceHarness(Protocol):
    """A backend's adapter to the contract: hand it a screen, get a driver showing it.

    Each backend realizes a requested screen its own way — `FakeDriver` takes the elements
    directly, a browser renders them, an app presents them — so the contract can drive the real
    driver instance (including any code that bypasses `drivers/base`), not the shared base alone.
    """

    backend: str

    def with_screen(self, elements: list[base.Element]) -> base.Driver:
        """Return a driver whose `query()` reports at least `elements`.

        A real backend may also surface chrome or container elements it was not asked to
        seed (a browser document, an app's navigation bar), so the contract requires the
        seeded elements to be present, not that the screen equals them exactly.
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
        # Two matches, no way to disambiguate: a single action must fail, not tap the first.
        driver = harness.with_screen([element(identifier="dup"), element(identifier="dup")])
        with pytest.raises(base.AmbiguousSelector):
            driver.tap({"id": "dup"})

    def test_zero_match_fails_rather_than_succeeding(self, harness: ConformanceHarness) -> None:
        driver = harness.with_screen([])
        with pytest.raises(base.ElementNotFound):
            driver.tap({"id": "missing"})

    def test_selector_failures_share_one_error_type(self, harness: ConformanceHarness) -> None:
        # Both failure modes are SelectorError, so a caller catches them uniformly on any backend.
        ambiguous = harness.with_screen([element(identifier="dup"), element(identifier="dup")])
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
        gestures = (
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
