"""A mid-scenario `extract` reads a settled value, not one still propagating (BE-0299 Unit 3).

A value an action mirrors into the accessibility tree can land a beat after the action returns
(Compose recomposes the `content-desc` asynchronously). A single post-step `query()` then races that
update: the resident channel's ~0.1 s read catches the pre-update value, binds it into `vars.*`, and a
later `assert` comparing against the live value fails a correct run — the exact CI flake this item
traces to. So `extract` polls `query()` until the properties it reads stop changing between two
consecutive reads, or a wall-clock deadline — a condition wait, no fixed sleep. Its budget is the
lane's wait floor (`BAJUTSU_MIN_WAIT_TIMEOUT`), the same knob every other condition wait honors: zero
(a single read, today's behavior) on lanes that don't set it, and the Android e2e lane's window where
the race lives.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.orchestrator import run_scenario
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import Scenario

_FLOOR = "BAJUTSU_MIN_WAIT_TIMEOUT"


class _LateMirrorDriver(FakeDriver):
    """A field whose read property keeps changing for a few reads after a tap, then rests.

    Models the async mirror that lands a beat after the action returns: each `query()` advances to
    the next value in `values`, holding the last once exhausted, so a single read catches a value
    still in flight while a settle poll rides out the change. `prop` picks which property moves
    (`value` or `label`) — the item's motivating case mirrors a counter into a `label`. Also tallies
    reads so a test can pin the single-read (no-floor) behavior.
    """

    def __init__(self, values: Sequence[str], *, prop: str = "value") -> None:
        self._prop = prop
        super().__init__([self._field(values[0])])
        self._values = list(values)
        self._i = 0
        self.queries = 0

    def _field(self, val: str) -> base.Element:
        return el("field", label=val) if self._prop == "label" else el("field", "Name", value=val)

    def query(self) -> list[base.Element]:
        self.queries += 1
        self.screen = [self._field(self._values[min(self._i, len(self._values) - 1)])]
        self._i += 1
        return super().query()


def _extract_then_assert_scenario(prop: str = "value") -> Scenario:
    # tap the field, extract its (still-propagating) property into vars.who, then assert the live
    # property equals what was captured — passes only if `extract` waited for it to settle first.
    return _scenario(
        {
            "name": "x",
            "steps": [
                {
                    "tap": {"id": "field"},
                    "extract": {"who": {"sel": {"id": "field"}, "prop": prop}},
                },
                {"assert": [{prop: {"sel": {"id": "field"}, "equals": "${vars.who}"}}]},
            ],
        }
    )


def test_extract_settles_on_an_async_mirrored_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")  # the lane floor gives `extract` a settle budget
    # Value moves for three reads (0 → 1 → 2), then rests at 2. The settle poll must bind the resting
    # "2", not the "0" a single read would capture — so the follow-up assert against the live "2" ok.
    driver = _LateMirrorDriver(["0", "1", "2", "2"])
    result = run_scenario(driver, _extract_then_assert_scenario(), clock=FakeClock())
    assert result.ok, result.failure


def test_extract_settles_on_an_async_mirrored_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The item's motivating case: a counter mirrored into a `label`, not a `value`. The settle
    # projection must track the `label` the extract names — a `value`-only projection would settle
    # at once on the static value and bind the still-moving label.
    driver = _LateMirrorDriver(["0", "1", "2", "2"], prop="label")
    result = run_scenario(driver, _extract_then_assert_scenario(prop="label"), clock=FakeClock())
    assert result.ok, result.failure


def test_extract_is_single_read_when_no_wait_floor_is_set() -> None:
    # Zero regression off the Android lane: with no floor the settle budget is zero, so `extract`
    # reads exactly once (no poll, no wall-clock cost) — today's behavior. The single read captures
    # the in-flight value, which is exactly why the lane that cares sets the floor.
    driver = _LateMirrorDriver(["0", "1", "2", "2"])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert driver.queries == 1  # the extract's one post-step read, no settle poll
    assert clock.now() == 0.0  # no sleep: the budget was zero


def test_seeded_wait_extract_refines_the_settled_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The non-mutating (seeded) branch: a `wait` settles on a tree, and the `extract` on the same
    # step must refine that seed until its value stops moving — not bind the seed's still-propagating
    # value. `wait` hands back the tree it settled on (BE-0259); the `initial=`-seeded settle poll
    # then rides the change out. Exercises the `initial=snapshot` path the mutating tests do not.
    driver = _LateMirrorDriver(["0", "1", "2", "2"])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "wait": {"for": {"id": "field"}, "timeout": 1.0},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    },
                    {"assert": [{"value": {"sel": {"id": "field"}, "equals": "${vars.who}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


class _MovingLabelStaticValueDriver(FakeDriver):
    """One field whose `label` moves for a few reads then rests, while its `value` stays constant."""

    def __init__(self, labels: Sequence[str]) -> None:
        super().__init__([el("field", labels[0], value="V")])
        self._labels = list(labels)
        self._i = 0

    def query(self) -> list[base.Element]:
        label = self._labels[min(self._i, len(self._labels) - 1)]
        self._i += 1
        self.screen = [el("field", label, value="V")]
        return super().query()


def test_extract_projection_covers_every_read_prop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # Two extracts on one step read `value` (static) and `label` (moving). The settle projection is
    # the UNION of both props, so it must keep polling while the label moves even though the value is
    # already stable — a projection that watched only one prop would settle early and bind a
    # still-moving label. The follow-up assert on the live label proves the union was honored.
    driver = _MovingLabelStaticValueDriver(["a", "b", "c", "c"])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {
                            "v": {"sel": {"id": "field"}, "prop": "value"},
                            "l": {"sel": {"id": "field"}, "prop": "label"},
                        },
                    },
                    {"assert": [{"label": {"sel": {"id": "field"}, "equals": "${vars.l}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure


class _SwappedDuplicateKeyDriver(FakeDriver):
    """A stable field plus two unidentified same-frame nodes returned in swapped order each read.

    The two noise nodes share an identifier (`None`) and a frame but differ in `value` — a
    duplicate-key pair. Their order flips between reads, as a real tree can reorder unidentified
    siblings. The extract target (`field`) never changes, so the settle must still converge: the
    projection has to key on the element *set*, not the read order, or the flipping noise makes the
    key differ every read and the poll burns the whole deadline.
    """

    def __init__(self) -> None:
        self._field = el("field", "Name", value="X")
        self._a = el(None, value="A", frame=(0.0, 0.0, 5.0, 5.0))
        self._b = el(None, value="B", frame=(0.0, 0.0, 5.0, 5.0))
        super().__init__([self._field, self._a, self._b])
        self._flip = False
        self.queries = 0

    def query(self) -> list[base.Element]:
        self.queries += 1
        noise = [self._a, self._b] if self._flip else [self._b, self._a]
        self._flip = not self._flip
        self.screen = [self._field, *noise]
        return super().query()


def test_extract_settle_converges_despite_reordered_duplicate_key_elements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The field is stable, so the extract value is correct either way — what this pins is convergence:
    # an (identifier, frame)-only sort would emit a different key each read as the noise flips, so the
    # settle would poll the full 5s deadline. Keying on the full projected row settles at once.
    driver = _SwappedDuplicateKeyDriver()
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    },
                    {"assert": [{"value": {"sel": {"id": "field"}, "equals": "${vars.who}"}}]},
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert clock.now() < 1.0  # converged in a couple of reads, not polled to the 5s deadline


class _AnimatingNoiseDriver(FakeDriver):
    """A stable extract target plus an unrelated element whose text animates on every read.

    Models a live-updating label elsewhere on the screen — a timer, a counter, a "Loading…"
    animation. It never stops changing, so a whole-screen prop projection would never converge; a
    target-scoped one settles as soon as the extract's own target is quiet.
    """

    def __init__(self) -> None:
        super().__init__([el("field", "Name", value="X"), el("timer", value="0")])
        self._tick = 0
        self.queries = 0

    def query(self) -> list[base.Element]:
        self.queries += 1
        self._tick += 1
        self.screen = [el("field", "Name", value="X"), el("timer", value=str(self._tick))]
        return super().query()


def test_extract_settle_ignores_unrelated_animating_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # A `timer` element animates its value every read while the extract target (`field`) is stable.
    # The projection is scoped to the target's read property, so the settle converges at once rather
    # than polling the whole 5s deadline waiting for an element the step never reads to go quiet.
    driver = _AnimatingNoiseDriver()
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    },
                    {"assert": [{"value": {"sel": {"id": "field"}, "equals": "${vars.who}"}}]},
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert clock.now() < 1.0  # converged; unrelated animating text did not hold the settle open


class _NeverRestingTargetDriver(FakeDriver):
    """The extract target's own value changes on every read — it never rests."""

    def __init__(self) -> None:
        super().__init__([el("field", "Name", value="0")])
        self._tick = 0
        self.queries = 0

    def query(self) -> list[base.Element]:
        self.queries += 1
        self._tick += 1
        self.screen = [el("field", "Name", value=str(self._tick))]
        return super().query()


def test_extract_settle_gives_up_at_the_deadline_when_target_never_rests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "1")
    # The target's own value changes every read, so the settle never converges: it must fall back to
    # best-effort at the wall-clock deadline (the fail-loud/traceable path) rather than spin forever.
    # Mirrors the driver-level test_settle_gives_up_at_the_wall_clock_deadline_when_never_stable.
    driver = _NeverRestingTargetDriver()
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure  # the extract still binds a best-effort value, not a failure
    assert clock.now() >= 1.0  # polled to the deadline, then gave up via the fallback branch


def _assert_step_scenario() -> Scenario:
    # tap, then a step-level `assert` on a value the tap mirrors in a beat late — the Unit 2 site
    # (distinct from the scenario-level `expect` that test_expect_wait covers).
    return _scenario(
        {
            "name": "x",
            "steps": [
                {"tap": {"id": "go"}},
                {"assert": [{"value": {"sel": {"id": "go.value"}, "equals": "1"}}]},
            ],
        }
    )


def test_assert_step_waits_for_an_async_mirrored_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")  # the lane floor gives the step-level assert a wait budget
    driver = FakeDriver([el("go", "Go", ["button"]), el("go.value", value="0")])

    def on_sleep(t: float) -> None:
        # The mirrored counter flips one poll after the action, as a fast resident read would observe.
        if t >= 0.1:
            driver.screen = [el("go", "Go", ["button"]), el("go.value", value="1")]

    result = run_scenario(driver, _assert_step_scenario(), clock=FakeClock(on_sleep))
    assert result.ok, result.failure


def test_assert_step_is_single_shot_when_no_wait_floor_is_set() -> None:
    # Zero regression off the Android lane: with no floor the step-level assert fails on the first
    # read, exactly as before — no poll, no wall-clock cost.
    driver = FakeDriver([el("go", "Go", ["button"]), el("go.value", value="0")])
    clock = FakeClock()
    result = run_scenario(driver, _assert_step_scenario(), clock=clock)
    assert not result.ok
    assert clock.now() == 0.0


def test_assert_step_fails_at_the_deadline_when_the_value_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "1")
    driver = FakeDriver([el("go", "Go", ["button"]), el("go.value", value="0")])
    clock = FakeClock()  # no on_sleep: the mirror never updates
    result = run_scenario(driver, _assert_step_scenario(), clock=clock)
    assert not result.ok
    # A bounded condition wait: it polled past the deadline rather than reading once or looping forever.
    assert clock.now() >= 1.0


# --- BE-0332 Unit 1: an actuation-anchored barrier for a backend whose reads lag the tap ---


class _LaggingCounterDriver(FakeDriver):
    """A counter field whose tree keeps naming the pre-tap value for the first reads after the tap.

    The Android read-lag in miniature (BE-0332): the tap advanced the counter, but the accessibility
    tree keeps publishing the previous value for a beat, so the first reads after the tap agree with
    each other on a value the tap already superseded. That agreeing-but-stale pair is exactly what a
    plain settle mistakes for a settled read; `read_lag()` is the budget the actuation-anchored
    barrier spends before it will trust one. `values` is walked one entry per read, holding the last.
    """

    def __init__(self, values: Sequence[str], *, lag: float) -> None:
        super().__init__([el("field", "Name", value=values[0])])
        self._values = list(values)
        self._i = 0
        self._lag = lag

    def read_lag(self) -> float:
        return self._lag

    def query(self) -> list[base.Element]:
        self.screen = [el("field", "Name", value=self._values[min(self._i, len(self._values) - 1)])]
        self._i += 1
        return super().query()


def test_extract_barrier_rejects_a_stale_but_stable_pair_after_a_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The extract.yaml failure in miniature: the tap made the counter "3", but the tree names the
    # pre-tap "2" for the first two reads, which agree with each other. A plain settle binds that
    # stale "2"; the follow-up assert against the live "3" then fails a correct run. The
    # actuation-anchored barrier holds the poll past the backend's lag, so it binds the "3" the tap
    # produced, and the assert passes.
    driver = _LaggingCounterDriver(["2", "2", "3", "3"], lag=0.3)
    clock = FakeClock()
    result = run_scenario(driver, _extract_then_assert_scenario(), clock=clock)
    assert result.ok, result.failure
    # The barrier is anchored on the lag (0.3), not the wait floor (5): it holds *past* the lag, then
    # releases as soon as an agreeing read postdates it — it does not sleep out the whole floor. A bug
    # that ignored `barrier` and always polled to the deadline would fail the upper bound; one that
    # released before the lag (the defect this closes) would fail the lower bound.
    assert 0.3 <= clock.now() < 5.0


def test_extract_without_a_declared_lag_keeps_the_plain_settle() -> None:
    # A backend that declares no read lag is unchanged: the first agreeing pair settles, with no extra
    # wait for an actuation window it never has. The value genuinely rests, so the pair is trustworthy.
    driver = _LateMirrorDriver(["3", "3"])  # a synchronous backend: no read_lag()
    assert not isinstance(driver, base.ReadLagProvider)
    clock = FakeClock()
    result = run_scenario(driver, _extract_then_assert_scenario(), clock=clock)
    assert result.ok, result.failure
    assert clock.now() == 0.0  # settled on the first agreeing pair, no barrier wait


def test_extract_barrier_returns_the_latest_read_when_the_tree_never_catches_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "1")
    # If the tree stays stale for the whole budget — the tap published nothing new — the barrier does
    # not raise or hang: it returns the latest read once the deadline is spent, best-effort, the same
    # as a plain settle that never stabilizes. The lag deliberately exceeds the budget so the barrier
    # can never be met; the step still binds the stale value and succeeds.
    driver = _LaggingCounterDriver(["2", "2", "2", "2"], lag=5.0)
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert (
        clock.now() >= 1.0
    )  # polled to the deadline, then returned latest via the fallback branch


def test_seeded_extract_is_not_held_by_the_actuation_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # A non-mutating step (`wait`) did not actuate, so its seeded extract has no actuation to postdate:
    # the barrier must not apply, even on a backend that declares a lag. The wait already settled the
    # tree (BE-0259), so the seed is trustworthy and the extract refines it without waiting out a
    # phantom lag window — otherwise every seeded extract on a lagging backend would pay the budget.
    driver = _LaggingCounterDriver(["7", "7"], lag=5.0)
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "wait": {"for": {"id": "field"}, "timeout": 1.0},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    },
                    {"assert": [{"value": {"sel": {"id": "field"}, "equals": "${vars.who}"}}]},
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert clock.now() < 5.0  # no barrier wait on the seeded path


# --- BE-0332 Unit 3: the read mark releases the actuation barrier early ---


class _MarkedLaggingCounterDriver(_LaggingCounterDriver):
    """A resident-style backend that reports read order as well as read lag (BE-0332 Unit 3).

    Extends `_LaggingCounterDriver` — it still names the pre-tap value for the first reads — but it also
    answers whether a read has caught up, as the resident reader's device event mark does. Reads before
    `caught_up_at` (a read count over this driver's life) predate the tap; from there on they postdate
    it. So the extract poll can release the instant the value is trustworthy instead of idling to the
    whole `read_lag()` budget a mark-less backend must spend.
    """

    def __init__(self, values: Sequence[str], *, lag: float, caught_up_at: int) -> None:
        super().__init__(values, lag=lag)
        self._caught_up_at = caught_up_at

    def read_postdates_actuation(self) -> bool:
        return self._i >= self._caught_up_at


def test_extract_barrier_is_not_released_by_a_mark_that_predates_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The `smoke (adb)` failure this closes: `step 4 (assert_): expected equals='2' but actual='3'`.
    # One gesture produces several accessibility events, so a read can postdate the tap and still carry
    # the previous value — Compose publishes the tapped button's own event before the Text mirroring the
    # new count recomposes. Here the mark fires from the very first read (`caught_up_at=1`) while the
    # counter still reads "2", and the read after it agrees, so the mark and the two-agreeing-reads test
    # both pass on a stale pair. Only the wall-clock barrier tells the two apart, so the poll must hold
    # for it and bind the live "3"; releasing on the mark bound the stale "2" and failed the next step.
    driver = _MarkedLaggingCounterDriver(["2", "2", "3", "3"], lag=5.0, caught_up_at=1)
    assert isinstance(driver, base.ReadOrderProvider)
    clock = FakeClock()
    result = run_scenario(driver, _extract_then_assert_scenario(), clock=clock)
    assert result.ok, result.failure
    assert clock.now() >= 5.0  # held for the barrier rather than releasing on the mark


def test_extract_barrier_still_bounds_by_the_budget_when_no_read_ever_postdates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "1")
    # The mark only ever tightens the wait: if the device never publishes — no read postdates the tap —
    # it cannot release the poll, so the wall-clock budget still bounds it and it returns the latest
    # read, best-effort. A marked backend is never worse off than a mark-less one when the mark simply
    # never arrives. The lag (5) exceeds the floor (1) so the barrier can never be met before the
    # deadline; caught_up_at is unreachable so the mark never fires either.
    driver = _MarkedLaggingCounterDriver(["2", "2", "2", "2"], lag=5.0, caught_up_at=999)
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "tap": {"id": "field"},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert (
        clock.now() >= 1.0
    )  # polled to the deadline, then returned latest via the fallback branch


class _UnorderedLaggingDriver(_LaggingCounterDriver):
    """A `ReadOrderProvider` whose actuation armed no mark barrier, so it never confirms an order.

    Models the mutating actuators that arm no catch-up (`type_text`, `back`, `tap_point`): the backend
    reports read order in general, but for this step there is no device mark to postdate, so
    `read_postdates_actuation` stays false and the extract poll must keep the Unit 1 wall-clock barrier
    rather than release on the first agreeing pair.
    """

    def read_postdates_actuation(self) -> bool:
        return False


def test_extract_barrier_holds_when_the_order_provider_never_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    # The regression the read-order predicate must not reintroduce: a backend that reports order but
    # whose actuation armed no device mark (a `type` step, say) must not get the early release. With
    # `read_postdates_actuation` false throughout, the wall-clock barrier still holds the stale "2" pair
    # past the lag and binds the live "3" — as a mark-less backend does. A predicate that read "no
    # barrier pending" as "caught up" would release on the first agreeing pair and bind the stale "2".
    driver = _UnorderedLaggingDriver(["2", "2", "3", "3"], lag=0.3)
    assert isinstance(driver, base.ReadOrderProvider)
    clock = FakeClock()
    result = run_scenario(driver, _extract_then_assert_scenario(), clock=clock)
    assert result.ok, result.failure
    assert (
        0.3 <= clock.now() < 5.0
    )  # the barrier held past the lag despite the ReadOrderProvider seam
