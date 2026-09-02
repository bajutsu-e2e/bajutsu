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
from pathlib import Path

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver, React
from bajutsu.common.evidence import Artifact, FileSink, NullSink
from bajutsu.common.evidence.intervals import Interval
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.orchestrator import run_scenario


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


class _KindsSink(NullSink):
    """Records the capture kinds requested per step, writing nothing.

    Subclasses `NullSink` rather than duck-typing the protocol so the loop recognizes it as a sink
    that reads nothing and skips materializing a tree for it — the counter then stays a pure measure
    of loop-issued reads.
    """

    def __init__(self) -> None:
        self.kinds_by_step: dict[str, list[str]] = {}

    def capture(
        self,
        driver: base.Driver,
        step_id: str,
        kinds: list[str],
        *,
        elements: list[base.Element] | None = None,
        elements_source: str | None = None,
    ) -> list[Artifact]:
        self.kinds_by_step[step_id] = kinds
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


def test_draining_the_actuation_record_issues_no_runner_read() -> None:
    # The actuation record must cost no device work: every value it carries is one the actuator already
    # had, and the drain itself only empties an in-memory log. So a step that records an actuation reads
    # the screen exactly as often as one that records none — the floor
    # `test_plain_tap_issues_no_runner_read` pins stays at zero.
    driver = _CountingDriver([el("go", "Go", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "go"}}]}),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 0
    assert len(result.steps[0].actuations) == 1  # the record was produced, and cost no read


def test_pre_step_baseline_issues_no_extra_runner_read() -> None:
    # The pre-step baseline capture (BE-0341) must defer to the sink exactly like the post-step one
    # already does: passing whatever `prev_after` holds, never forcing a `query()` of its own. A
    # sink that does not consume `elements` (like `test_plain_tap_issues_no_runner_read`'s) pays
    # nothing for either baseline, so the loop's own read count stays at the pre-existing floor.
    driver = _CountingDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}]}),
        clock=FakeClock(),
        sink=_KindsSink(),
    )
    assert result.ok
    assert driver.queries == 0


def test_a_writing_sink_pays_one_read_per_step_not_two(tmp_path: Path) -> None:
    # A sink that writes `elements` reads the tree once per step, not once per capture call. The
    # post-step read goes through `_ScreenRead` rather than being left to `write_elements`, so it is
    # counted *and* seeds `prev_after` — which the next step's pre-step baseline then reuses instead
    # of reading again. Leaving it to the sink would cost two uncounted reads per step (~2.4s each on
    # adb) and keep `prev_after` unset for the whole scenario, defeating BE-0234 Unit 2's reuse.
    # Two steps therefore cost three reads: step0's baseline (nothing to reuse yet) plus one
    # post-step read each.
    driver = _CountingDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}]}),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok
    assert driver.queries == 3


def test_a_writing_sink_adds_no_read_to_the_before_reuse(tmp_path: Path) -> None:
    # The `before` reuse measured under `_KindsSink` has to hold on the path a real run takes. That
    # sink is a `NullSink`, which the always-on `elements` write structurally exempts from the
    # post-step read (`loop.py`'s `not isinstance(self.cfg.sink, NullSink)`), so every count in this
    # file is taken on the exempt branch; a `FileSink` takes the writing one. Four reads, and which
    # four is the point: step0's pre-step baseline, the initial `before`, then one post-step read
    # per step. The screenChanged policy already forces that post-step read, so writing `elements`
    # consumes it rather than adding a second — step1 pays one read, not two, and reuses both its
    # baseline and its `before` from step0's `after`.
    #
    # The first two are one pre-action screen read twice: the baseline's `elements` write issues its
    # own `query()` inside `evidence.capture` (it is handed `elements=None` with no `prev_after` to
    # reuse yet), and the `before` comparison then reads the same unacted-on screen. That pair
    # predates this change — the merge base counts the same 4 here — so it is BE-0341's to remove,
    # not this test's to hide.
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
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok
    assert driver.queries == 4


def test_a_writing_sink_reuses_the_asserts_evaluated_tree(tmp_path: Path) -> None:
    # BE-0259's seed reuse, likewise measured on the writing branch: the `assert` already queried a
    # tree to evaluate itself, and the always-on `elements` write consumes that seed instead of
    # re-reading. One step therefore costs its pre-step baseline plus the assert's own query — 2,
    # where a post-step capture that re-read for the write would take 3.
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
        sink=FileSink(tmp_path / "run1"),
    )
    assert result.ok, result.failure
    assert driver.queries == 2


def test_pre_step_baseline_skips_the_web_query_under_a_null_sink() -> None:
    # A `web` block's first nested step must not force a bridge query for a baseline `NullSink`
    # discards (review follow-up on BE-0341): under the default sink (`NullSink`, `sink=None`),
    # the only bridge read left is the pre-existing, unrelated post-step read every web-block step
    # already pays (BE-0234 Unit 2, `screen.get()` for a web `active_driver`) — one call for one
    # step, not two.
    class _CountingBridge:
        def __init__(self) -> None:
            self.calls = 0

        def query_dom(self, webview_id: str) -> list[base.Element]:
            self.calls += 1
            return []

        def tap_element(self, webview_id: str, point: tuple[float, float]) -> None:
            pass

        def type_text(self, webview_id: str, text: str) -> None:
            pass

        def scroll_to(self, webview_id: str, element_id: str) -> None:
            pass

    bridge = _CountingBridge()
    driver = _CountingDriver([el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [{"type": {"text": "hi"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.calls == 1


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
    # screen), not a stale earlier tree that would make step 2 look changed too. The rule requests
    # `actionLog` rather than a `screenshot` modifier: the pre-step baseline always fires
    # `screenshot.before` and the scenario's last step (step 1 here) additionally always fires
    # `screenshot.after` (BE-0341), so neither modifier can tell "the rule fired" apart from "every
    # step's own baseline."
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
                "capturePolicy": [{"on": {"event": "screenChanged"}, "capture": ["actionLog"]}],
            }
        ),
        clock=FakeClock(),
        sink=sink,
    )
    assert result.ok
    assert "actionLog" in sink.kinds_by_step["x/step0"]
    assert "actionLog" not in sink.kinds_by_step["x/step1"]


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
