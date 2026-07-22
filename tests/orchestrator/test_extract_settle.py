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

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario

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


def _extract_then_assert_scenario(prop: str = "value") -> object:
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


def _assert_step_scenario() -> object:
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
