"""BE-0234 / BE-0259 read-count yardstick: the run loop takes the minimum screen reads per step.

On the adb backend a screen read (`uiautomator dump`) costs ~2.4s, so a redundant `query()` is the
dominant per-step waste. These tests pin the reductions — BE-0234's lazy end-of-step read and
`before`-reuse, and BE-0259's reuse of the tree a non-mutating step already settled on — as
behavior, so a future change that reintroduces a redundant read is caught on the fast gate. They
count runner-issued reads via a FakeDriver that tallies `query()` (the loop is its only caller); the
adb driver's internal `_settle` reads are counted separately in `tests/test_adb.py`.
"""

from __future__ import annotations

from collections.abc import Sequence

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver, React
from bajutsu.evidence import Artifact
from bajutsu.evidence.intervals import Interval
from bajutsu.evidence.network import NetworkExchange
from bajutsu.orchestrator import run_scenario
from bajutsu.orchestrator.waits import WaitTrace


class _CountingDriver(FakeDriver):
    """A FakeDriver that tallies every `query()`; in the run loop the loop is the only caller."""

    def __init__(
        self,
        screen: Sequence[base.Element] | None = None,
        react: React | None = None,
        exchanges: Sequence[NetworkExchange] | None = None,
    ) -> None:
        super().__init__(screen, react, exchanges)
        self.queries = 0

    def query(self) -> list[base.Element]:
        self.queries += 1
        return super().query()


class _KindsSink:
    """Records the capture kinds requested per step (a NullSink that reads nothing, so it never
    forces the loop to materialize a tree — the counter stays a pure measure of loop-issued reads)."""

    def __init__(self) -> None:
        self.kinds_by_step: dict[str, list[str]] = {}

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
    ) -> list[Artifact]:
        self.kinds_by_step[step_id] = kinds
        return []

    def wait_diagnostic(
        self, step_id: str, *, trace: WaitTrace, elements: list[base.Element]
    ) -> Artifact | None:
        return None

    def start_scenario_intervals(self, scenario_id: str, kinds: list[str]) -> list[Interval]:
        return []

    def finish_scenario_intervals(
        self, scenario_id: str, started: list[Interval]
    ) -> list[Artifact]:
        return []


def test_plain_tap_issues_no_runner_read() -> None:
    # No screenChanged policy, no extract, a sink that reads nothing: no consumer needs the post-step
    # tree, so the loop reads the screen zero times — the ~2.4s adb read Unit 2 removes per step.
    driver = _CountingDriver([el("go", "Go", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "go"}}]}),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 0


def test_screen_changed_reuses_previous_after_as_before() -> None:
    # With a screenChanged policy every step needs a `before`, but the previous step's `after` is the
    # same device state, so it is reused: one initial `before` plus one post-step read per step —
    # 1 + 2 = 3 for two steps, not the 4 a re-read `before` would cost.
    driver = _CountingDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 3


def test_extract_forces_a_single_post_step_read() -> None:
    # No screenChanged policy, so no `before`; the extract is the only consumer, so exactly one read.
    driver = _CountingDriver([el("field", "Name", value="Ada")])
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
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 1


def test_before_reuse_detects_screen_change_per_step() -> None:
    # Correctness of the reuse: step 1 changes the screen, step 2 does not. screenChanged must fire
    # for step 1 only — which holds only if step 2's reused `before` is step 1's `after` (the changed
    # screen), not a stale earlier tree that would make step 2 look changed too.
    changed = [el("next", "Next"), el("b", "B", ["button"])]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"id": "a"}:
            d.screen = changed  # step 1 navigates; step 2's tap leaves the screen as-is

    driver = _CountingDriver([el("a", "A", ["button"]), el("b", "B", ["button"])], react=react)
    sink = _KindsSink()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        clock=FakeClock(),
        sink=sink,
    )
    assert result.ok
    assert "screenshot.before" in sink.kinds_by_step["x/step0"]
    assert "screenshot.before" not in sink.kinds_by_step["x/step1"]


def test_assert_with_extract_reuses_the_evaluated_tree() -> None:
    # An `assert` queries the tree to evaluate itself; the `extract` on the same step reads that
    # SAME settled tree rather than re-querying — one read end to end, down from two before BE-0259.
    driver = _CountingDriver([el("field", "Name", value="Ada")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "assert": [{"exists": {"id": "field"}}],
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok, result.failure
    assert driver.queries == 1


def test_assert_under_screen_changed_reuses_the_evaluated_tree() -> None:
    # With a screenChanged policy the step needs an `after` to diff against `before`; the assert's
    # own query supplies it, so the step costs one `before` read plus the assert's read — 2, not the
    # 3 a separate post-step read would add (BE-0259).
    driver = _CountingDriver([el("go", "Go", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"assert": [{"exists": {"id": "go"}}]}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged"}, "capture": ["screenshot.before"]}
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 2


def test_wait_reuses_its_settled_tree() -> None:
    # A `wait for` settles on a tree; the extract on the same step reads THAT tree, not a fresh one
    # — the wait is non-mutating, so its last query is the step's `after` (BE-0259).
    driver = _CountingDriver([el("field", "Name", value="Ada")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "wait": {"for": {"id": "field"}, "timeout": 1.0},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok, result.failure
    assert driver.queries == 1


def test_action_step_reads_a_fresh_after_never_a_reused_snapshot() -> None:
    # An action can change the screen, so its post-step read must be FRESH: a tap navigates to a new
    # value, and the extract on that same step must read the NEW screen. If the action's `after` were
    # (wrongly) reused from a pre-action snapshot, ${vars.who} would bind the stale value and the
    # follow-up assert would fail. Confirms BE-0259 seeds only non-mutating steps.
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = [el("field", "Name", value="Grace")]

    driver = _CountingDriver([el("field", "Name", value="Ada")], react=react)
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
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok, result.failure


def test_wait_gone_reuses_its_settled_tree() -> None:
    # The reuse is not `for`-only: `wait until: gone` also hands back its last-polled tree, so the
    # extract on the same step reads THAT tree — one read, not two. Guards the non-`for` wait variants
    # against a regression that returns no snapshot and reintroduces a redundant query (BE-0259).
    driver = _CountingDriver(
        [el("field", "Name", value="Ada")]
    )  # the awaited "ghost" is already gone
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "wait": {"until": {"gone": {"id": "ghost"}}, "timeout": 1.0},
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok, result.failure
    assert driver.queries == 1


def test_wait_until_request_reads_a_fresh_after() -> None:
    # `wait until: request` polls the observed network, not the tree, so it hands back no snapshot
    # (None) — the one non-mutating wait whose screen may still be rendering as the awaited response
    # lands. The extract on the same step must therefore issue a FRESH read: exactly one query, not
    # the zero a wrongly-reused snapshot would cost (BE-0259).
    driver = _CountingDriver([el("field", "Name", value="Ada")])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "wait": {
                            "until": {
                                "request": {"method": "GET", "path": "/items", "status": 200}
                            },
                            "timeout": 1.0,
                        },
                        "extract": {"who": {"sel": {"id": "field"}, "prop": "value"}},
                    }
                ],
            }
        ),
        clock=FakeClock(),
        sink=_KindsSink(),
        network=lambda: [NetworkExchange(method="GET", path="/items", status=200)],
    )
    assert result.ok, result.failure
    assert driver.queries == 1
