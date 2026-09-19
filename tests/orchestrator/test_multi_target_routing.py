"""Tests for routing a scenario's steps across several live targets (BE-0428).

The step loop drives one `_StepRunner` per declared target over one shared `StepLoopState`, so
every target sees the same step numbering, the same outcome list, and — the point of the feature —
the same `${vars.*}` dict. Each target has its own `FakeDriver` here, so "this step ran against the
right target" is checkable directly: a driver only records the actions actually dispatched to it.

Nothing an LLM decides enters any of this. Routing reads `step.target`, a declared field the
load-time validator already checked (prime directive 1).
"""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.orchestrator import RunResult, TargetRuntime, run_scenario
from bajutsu.common.scenario import Scenario

_APP_SCREEN = [el("app.button", label="tap me"), el("app.value", value="7")]
_WEB_SCREEN = [el("web.button", label="click me"), el("web.value", value="7")]


def _runtimes(**drivers: FakeDriver) -> dict[str, TargetRuntime]:
    """One runtime per named target, each over its own driver and a sink that writes nothing."""
    return {name: TargetRuntime(driver=driver, sink=NullSink()) for name, driver in drivers.items()}


def _run(scenario: Scenario, **drivers: FakeDriver) -> RunResult:
    """Run *scenario* with one target per keyword argument; the first is the primary."""
    primary = next(iter(drivers))
    return run_scenario(
        drivers[primary],
        scenario,
        FakeClock(),
        target_runtimes=_runtimes(**drivers),
        primary_target=primary,
    )


def _taps(driver: FakeDriver) -> list[object]:
    """The selectors this driver was actually asked to tap."""
    return [arg for kind, arg in driver.actions if kind == "tap"]


def test_each_step_is_dispatched_to_its_own_targets_driver() -> None:
    # The headline case: two targets interleaved, not two contiguous blocks. Each driver must see
    # exactly its own steps, in order — a driver that saw the other's would mean routing fell back
    # to the primary.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = _run(
        _scenario(
            {
                "name": "interleaved",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "tap": {"id": "app.button"}},
                    {"target": "web", "tap": {"id": "web.button"}},
                    {"target": "app", "tap": {"id": "app.value"}},
                    {"target": "web", "tap": {"id": "web.value"}},
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert r.ok, r.failure
    assert _taps(app) == [{"id": "app.button"}, {"id": "app.value"}]
    assert _taps(web) == [{"id": "web.button"}, {"id": "web.value"}]


def test_every_step_carries_the_target_it_ran_against() -> None:
    # The report's half of the same fact: `StepOutcome.target` names the declared target, so a
    # reader can tell which platform produced each row without re-reading the scenario file.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = _run(
        _scenario(
            {
                "name": "labelled",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "tap": {"id": "app.button"}},
                    {"target": "web", "tap": {"id": "web.button"}},
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert [o.target for o in r.steps] == ["app", "web"]


def test_one_declared_target_still_labels_its_steps() -> None:
    # A scenario declaring exactly one target lets its steps omit the name, but they still ran
    # against a named target — leaving the report blank there would make it look like a run that
    # declared none.
    app = FakeDriver(screen=list(_APP_SCREEN))
    r = _run(
        _scenario({"name": "single", "targets": ["app"], "steps": [{"tap": {"id": "app.button"}}]}),
        app=app,
    )
    assert [o.target for o in r.steps] == ["app"]


def test_a_scenario_declaring_no_targets_leaves_the_label_empty() -> None:
    # The "empty means not applicable" convention: nothing changes for a scenario that declares no
    # targets, so an existing report keeps reading exactly what it did before.
    app = FakeDriver(screen=list(_APP_SCREEN))
    r = run_scenario(
        app, _scenario({"name": "legacy", "steps": [{"tap": {"id": "app.button"}}]}), FakeClock()
    )
    assert [o.target for o in r.steps] == [""]


def test_steps_share_one_numbering_across_targets() -> None:
    # One `StepLoopState` spans every target, so evidence `step_id`s stay unique across the
    # scenario. Per-target counters would give two targets' first steps the same id, and the second
    # would overwrite the first's screenshots.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = _run(
        _scenario(
            {
                "name": "numbered",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "tap": {"id": "app.button"}},
                    {"target": "web", "tap": {"id": "web.button"}},
                    {"target": "app", "tap": {"id": "app.value"}},
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert [o.index for o in r.steps] == [0, 1, 2]


def test_a_value_one_target_extracts_reaches_an_assertion_on_another() -> None:
    # The motivation's own second claim: `live_bindings` is one dict per `run_scenario` call, shared
    # by every target's runner, so a value the app-side step captured names the record the web-side
    # assertion checks. Without this, a cross-platform test could only assert that *some* value
    # changed, never that it was the right one.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = _run(
        _scenario(
            {
                "name": "shared-vars",
                "targets": ["app", "web"],
                "steps": [
                    {
                        "target": "app",
                        "tap": {"id": "app.button"},
                        "extract": {"picked": {"sel": {"id": "app.value"}}},
                    },
                    {"target": "web", "tap": {"id": "web.value"}},
                ],
                "expect": [
                    {
                        "target": "web",
                        "value": {"sel": {"id": "web.value"}, "equals": "${vars.picked}"},
                    }
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert r.ok, r.failure
    assert [a.ok for a in r.expect_results] == [True]


def test_expect_is_polled_against_each_entrys_own_target() -> None:
    # `expect` groups by target and polls each one's own driver, so an assertion naming the web
    # target is never checked against the app target's tree — which is what makes "act here, verify
    # there" a single verdict rather than two runs a reviewer reconciles by hand.
    app = FakeDriver(screen=[el("shared.value", value="app-side")])
    web = FakeDriver(screen=[el("shared.value", value="web-side")])
    r = _run(
        _scenario(
            {
                "name": "per-target-expect",
                "targets": ["app", "web"],
                "steps": [{"target": "app", "tap": {"id": "shared.value"}}],
                "expect": [
                    {
                        "target": "app",
                        "value": {"sel": {"id": "shared.value"}, "equals": "app-side"},
                    },
                    {
                        "target": "web",
                        "value": {"sel": {"id": "shared.value"}, "equals": "web-side"},
                    },
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert r.ok, r.failure
    assert [a.target for a in r.expect_results] == ["app", "web"]


def test_expect_results_come_back_in_declared_order() -> None:
    # Grouping by target regroups the polls, never the results: a reader sees the block as written.
    app = FakeDriver(screen=[el("shared.value", value="app-side")])
    web = FakeDriver(screen=[el("shared.value", value="web-side")])
    r = _run(
        _scenario(
            {
                "name": "order",
                "targets": ["app", "web"],
                "steps": [{"target": "app", "tap": {"id": "shared.value"}}],
                "expect": [
                    {
                        "target": "web",
                        "value": {"sel": {"id": "shared.value"}, "equals": "web-side"},
                    },
                    {
                        "target": "app",
                        "value": {"sel": {"id": "shared.value"}, "equals": "app-side"},
                    },
                    {"target": "web", "exists": {"sel": {"id": "shared.value"}}},
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert [a.target for a in r.expect_results] == ["web", "app", "web"]


def test_a_failing_step_on_the_second_target_fails_the_scenario() -> None:
    # One verdict for the whole scenario, which is the point: a web-side miss fails the run the
    # same way an app-side one does, rather than passing because the app half was fine.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = _run(
        _scenario(
            {
                "name": "second-fails",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "tap": {"id": "app.button"}},
                    {"target": "web", "tap": {"id": "web.missing"}},
                ],
            }
        ),
        app=app,
        web=web,
    )
    assert not r.ok
    assert r.failure is not None
    assert "web.missing" in r.failure


def test_a_step_inside_a_web_block_stays_on_its_blocks_own_target() -> None:
    # A step nested inside a `web:` block carries no `target` of its own — its active driver is the
    # block's `WebContextDriver`, and falling back to the primary target's driver would silently run
    # it against the app surface underneath the WebView.
    app = FakeDriver(screen=list(_APP_SCREEN))
    web = FakeDriver(screen=[el("web.host", label="host")])
    r = _run(
        _scenario(
            {
                "name": "nested-web",
                "targets": ["app", "web"],
                "steps": [
                    {"target": "app", "tap": {"id": "app.button"}},
                    {
                        "target": "web",
                        "web": {
                            "within": {"id": "web.host"},
                            "steps": [{"tap": {"id": "dom.go"}}],
                        },
                    },
                ],
            }
        ),
        app=app,
        web=web,
    )
    # The block's own nested step never reaches the app target, whatever it did inside the bridge.
    assert _taps(app) == [{"id": "app.button"}]
    assert all(o.target in {"app", "web"} for o in r.steps)
