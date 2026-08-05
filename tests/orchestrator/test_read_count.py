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

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver, React
from bajutsu.evidence import Artifact, FileSink
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


class _CountingBridge:
    """A fake WebView bridge that tallies every `query_dom()` call — the `web`-block analogue of
    `_CountingDriver`, since a `WebContextDriver` resolves through the bridge, never the native
    driver's `query()`."""

    def __init__(self, dom_elements: Sequence[base.Element]) -> None:
        self._elements = list(dom_elements)
        self.queries = 0

    def query_dom(self, webview_id: str) -> list[base.Element]:
        self.queries += 1
        return list(self._elements)

    def tap_element(self, webview_id: str, point: base.Point) -> None:
        pass

    def type_text(self, webview_id: str, text: str) -> None:
        pass

    def scroll_to(self, webview_id: str, element_id: str) -> None:
        pass


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


def test_pre_step_baseline_issues_no_extra_runner_read() -> None:
    # The pre-step baseline capture (BE-XXXX) must defer to the sink exactly like the post-step one
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


def test_pre_step_baseline_skips_the_web_query_under_a_null_sink() -> None:
    # A `web` block's first nested step must not force a bridge query for a baseline `NullSink`
    # discards (review follow-up on BE-XXXX): under the default sink (`NullSink`, `sink=None`), a
    # `type` step (no tap to resolve, no extract) now costs the bridge nothing at all — the
    # post-step laziness fix below closed the one remaining forced read this test used to pin
    # ("one call for one step, not two"; see test_web_block_plain_tap_issues_no_extra_bridge_query
    # for the tap-resolve case, which still costs exactly one).
    driver = _CountingDriver([el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))])
    bridge = _CountingBridge([])
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
    assert bridge.queries == 0


def test_web_block_plain_tap_issues_no_extra_bridge_query() -> None:
    # The `web`-block analogue of test_plain_tap_issues_no_runner_read: no screenChanged policy, no
    # extract, the default `NullSink` (the pre-step baseline's own laziness only recognizes a real
    # `NullSink`, not just any sink that happens to read nothing — see
    # test_pre_step_baseline_skips_the_web_query_under_a_null_sink). The tap itself must still
    # resolve its target through the bridge (1 query, same as any tap), but the post-step capture —
    # previously unconditional for a `web` block regardless of whether any consumer needed it — must
    # add none on top.
    native_screen = [el("app.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0))]
    driver = _CountingDriver(native_screen)
    bridge = _CountingBridge([el("go", "Go", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"web": {"within": {"id": "app.webview"}, "steps": [{"tap": {"id": "go"}}]}}
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.queries == 1  # the tap's own resolve only; no forced post-step read
    assert driver.queries == 1  # resolving the `within` host element only


def test_web_block_file_sink_skips_the_post_step_read_with_no_capture_policy(
    tmp_path: Path,
) -> None:
    # The `elements`-in-`instant` half of `wants_web_elements`, isolated: a real `FileSink` (so the
    # `NullSink` half of the guard cannot carry the test) whose scenario asks for no post-step
    # capture at all must still skip the web read — only the block's first nested step's pre-step
    # baseline touches the bridge. Dropping the `elements`-fired clause shows up here as 2 queries,
    # not 1 — the PR's headline case ("a step whose capturePolicy never fired the elements capture")
    # otherwise has no test discriminating it.
    driver = FakeDriver([el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))])
    bridge = _CountingBridge([])
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
        sink=FileSink(tmp_path / "run1"),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.queries == 1  # the pre-step baseline only; no post-step read


def test_web_block_null_sink_skips_the_post_step_read_even_when_elements_fires() -> None:
    # The `NullSink` half of `wants_web_elements`, isolated: a `capturePolicy` rule really does fire
    # `elements` post-step, so only the `isinstance(..., NullSink)` clause keeps the bridge read
    # from happening — a sink that reads nothing must pay nothing. Dropping that clause shows up
    # here as 1 query, not 0.
    driver = FakeDriver([el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))])
    bridge = _CountingBridge([])
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
                "capturePolicy": [{"on": {"action": "type"}, "capture": ["elements"]}],
            }
        ),
        clock=FakeClock(),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.queries == 0


def test_web_block_file_sink_reads_the_web_tree_not_the_native_one(tmp_path: Path) -> None:
    # A FileSink genuinely needs the tree, so the deferred read this laziness relies on must still
    # fire — and against the *active* (web) driver, not the native one passed to `capture()` for
    # `screenshot` (a `WebContextDriver` can't take one). The always-on `elements` baseline is
    # captured *pre*-step (BE-XXXX), so a post-step `elements` fires only when the scenario asks for
    # it: the `capturePolicy` below is what makes the deferred web read actually fire.
    # Guards against reintroducing the wrong-driver class of bug already fixed once for the
    # pre-step baseline capture.
    native_screen = [
        el("app.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0)),
        el("native-only", "Native Only", ["button"]),
    ]
    driver = FakeDriver(native_screen)
    bridge = _CountingBridge([el("go", "Go", ["button"])])
    run_dir = tmp_path / "run1"
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"web": {"within": {"id": "app.webview"}, "steps": [{"tap": {"id": "go"}}]}}
                ],
                "capturePolicy": [{"on": {"action": "tap"}, "capture": ["elements"]}],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(run_dir),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    # step0 is the `web` block itself; step1 is the tap nested inside it (BE-0172 shares one
    # monotonic counter across the nesting).
    written = (run_dir / "x" / "step1" / "elements.json").read_text(encoding="utf-8")
    assert "go" in written  # the web DOM element the step actually acted on
    assert "native-only" not in written  # never the native tree the sink can't screenshot with
    # …and the deferred web read really fired, rather than the assertions above passing on the
    # pre-step baseline's own write: pre-step baseline + the tap's resolve + the post-step capture.
    assert bridge.queries == 3


def test_web_block_elements_capture_reuses_the_read_for_the_next_steps_baseline(
    tmp_path: Path,
) -> None:
    # A real post-step `elements` capture must go through `screen.get()`, not the sink's own
    # `elements=None` fallback: `capture()` is handed the *native* `self.cfg.driver`, so the
    # fallback would query that driver — writing the native tree and leaving `prev_after` (seeded
    # from `screen.cached`) `None`, so the *next* nested step's pre-step baseline pays its own
    # bridge query — 2 reads per step boundary instead of 1. `type` steps (no target to resolve)
    # isolate the count to exactly the pre-step/post-step captures, and a real `FileSink` (not a
    # sink that discards `elements`, like `_KindsSink`) is required so its own `elements=None`
    # fallback is actually reachable — a regression back to it shows up as 2 bridge queries here
    # (one per step's pre-step baseline, since the fallback never touches the bridge) writing the
    # *native* tree to `elements.json` both times, instead of the correct 3 (one pre-step baseline
    # read on the block's first nested step, whose `prev_after` is reset around the block, plus one
    # post-step read per `type` step — each of those carried forward as the next step's pre-step
    # baseline, which is what keeps it at 3).
    driver = FakeDriver([el("app.webview", frame=(0.0, 0.0, 400.0, 800.0))])
    bridge = _CountingBridge([])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "app.webview"},
                            "steps": [{"type": {"text": "a"}}, {"type": {"text": "b"}}],
                        }
                    }
                ],
                "capturePolicy": [{"on": {"action": "type"}, "capture": ["elements"]}],
            }
        ),
        clock=FakeClock(),
        sink=FileSink(tmp_path / "run1"),
        webview_bridge=bridge,
    )
    assert result.ok, result.failure
    assert bridge.queries == 3


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
    # `screenshot.after` (BE-XXXX), so neither modifier can tell "the rule fired" apart from "every
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
