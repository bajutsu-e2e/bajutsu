"""Tests for routing a scenario's steps across several live targets (BE-0428).

The step loop drives one `_StepRunner` per declared target over one shared `StepLoopState`, so
every target sees the same step numbering, the same outcome list, and — the point of the feature —
the same `${vars.*}` dict. Each target has its own `FakeDriver` here, so "this step ran against the
right target" is checkable directly: a driver only records the actions actually dispatched to it.

Nothing an LLM decides enters any of this. Routing reads `step.target`, a declared field the
load-time validator already checked (prime directive 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _orch import FakeClock, _scenario
from conftest import el, guard_rule

from bajutsu.common.assertions import EvalContext, VisualContext
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.evidence.redaction import Redactor
from bajutsu.common.evidence.sink import RunArtifactWriter
from bajutsu.common.orchestrator import AlertGuardConfig, RunResult, TargetRuntime, run_scenario
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


class _FakeControl:
    """A `DeviceControl` double that reports one fixed clipboard value."""

    def __init__(self, clipboard: str) -> None:
        self._clipboard = clipboard

    def get_clipboard(self) -> str:
        return self._clipboard

    def set_location(self, lat: float, lon: float) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def push(self, payload: dict[str, object]) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def clear_keychain(self) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def clear_clipboard(self) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def set_clipboard(self, text: str) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def home(self) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def foreground(self) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def override_status_bar(self, **kwargs: str | int) -> None:  # pragma: no cover — unused here
        raise NotImplementedError

    def clear_status_bar(self) -> None:  # pragma: no cover — unused here
        raise NotImplementedError


def test_a_clipboard_expect_entry_reads_its_own_targets_control() -> None:
    # A `clipboard` assertion in `expect` naming a non-primary target must read that target's own
    # pasteboard, not the primary's — the two devices' clipboards are independent (BE-0428).
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    app_control, web_control = _FakeControl("app-clip"), _FakeControl("web-clip")
    r = run_scenario(
        app,
        _scenario(
            {
                "name": "clipboard-per-target",
                "targets": ["app", "web"],
                "steps": [{"target": "app", "tap": {"id": "app.button"}}],
                "expect": [
                    {"target": "app", "clipboard": {"equals": "app-clip"}},
                    {"target": "web", "clipboard": {"equals": "web-clip"}},
                ],
            }
        ),
        FakeClock(),
        control=app_control,
        target_runtimes={
            "app": TargetRuntime(driver=app, sink=NullSink(), control=app_control),
            "web": TargetRuntime(driver=web, sink=NullSink(), control=web_control),
        },
        primary_target="app",
    )
    assert r.ok, r.failure
    assert [a.ok for a in r.expect_results] == [True, True]


def _alert_button(label: str) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
        "nativeZ": None,
    }


def test_alert_guard_retry_polls_every_expect_entry_against_its_own_target() -> None:
    # BE-0428 regression: the retry `_evaluate_expect` run after a guard dismissal must forward
    # `target_runtimes`/`primary_target` exactly like the first evaluation. Before the fix, the
    # retry call site dropped both, so every entry — the `web` one included — was silently
    # re-polled against the primary's driver instead of its own.
    #
    # The guard itself only ever inspects the *primary* target's driver (it is a property of the
    # scenario, not of any one target), so `app` carries a real system-alert button for it to
    # dismiss; `web` starts with the wrong value so the first `expect` evaluation fails and the
    # guard fires at all, and the dismissal's own `react` hook flips it to the right one — standing
    # in for whatever the alert was covering on that platform's own client.
    def react(d: FakeDriver, kind: str, _arg: object) -> None:
        if kind == "handle_system_alert":
            d.system_alert_buttons = []  # the alert cleared
            web.screen[0] = el("shared.value", value="web-side")

    app = FakeDriver(screen=[el("shared.value", value="app-side")], react=react)
    app.system_alert_buttons = [_alert_button("Allow")]
    web = FakeDriver(screen=[el("shared.value", value="not-yet-set")])

    r = run_scenario(
        app,
        _scenario(
            {
                "name": "retry-routes-per-target",
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
        FakeClock(),
        alert_guard=AlertGuardConfig(rules=[guard_rule("Allow")]),
        target_runtimes={
            "app": TargetRuntime(driver=app, sink=NullSink()),
            "web": TargetRuntime(driver=web, sink=NullSink()),
        },
        primary_target="app",
    )
    # If the retry fell back to the primary (`app`)'s driver for the `web` entry, that entry would
    # keep reading "app-side" off the app driver and never pass.
    assert r.ok, r.failure
    assert [a.target for a in r.expect_results] == ["app", "web"]


def _visual_ctx(tmp_path: Path, prefix: str) -> EvalContext:
    """A real `VisualContext` under *tmp_path*, scoped to *prefix* — the same per-target prefix
    `_eval_context_for` (pipeline.py) reserves for a non-primary target's own capture."""
    shot_dir = tmp_path / prefix
    shot_dir.mkdir(parents=True, exist_ok=True)
    return EvalContext(
        visual=VisualContext(
            screenshot_path=shot_dir / "visual-actual.png",
            baselines_dir=tmp_path / "baselines",
            writer=RunArtifactWriter(tmp_path, Redactor(None)),
            prefix=prefix,
        )
    )


def test_a_visual_expect_entry_captures_its_own_targets_screen(tmp_path: Path) -> None:
    # BE-0428 regression: `_capture_visual_actual` used to run once, against the primary's driver
    # and context, before `_evaluate_expect` was even called — so a `visual` entry naming a second
    # target read a screenshot that was never taken (`ctx.visual.screenshot_path` was reserved by
    # `_eval_context_for` but nothing wrote it), raising `FileNotFoundError` out of the assertion
    # rather than a clean pass/fail. The fix moves the capture inside `_evaluate_expect`, once per
    # referenced target, using that target's own driver and context.
    app, web = FakeDriver(screen=list(_APP_SCREEN)), FakeDriver(screen=list(_WEB_SCREEN))
    r = run_scenario(
        app,
        _scenario(
            {
                "name": "visual-per-target",
                "targets": ["app", "web"],
                "steps": [{"target": "app", "tap": {"id": "app.button"}}],
                "expect": [
                    {"target": "app", "visual": {"baseline": "app.png"}},
                    {"target": "web", "visual": {"baseline": "web.png"}},
                ],
            }
        ),
        FakeClock(),
        ctx=_visual_ctx(tmp_path, "00-s"),
        target_runtimes={
            "app": TargetRuntime(driver=app, sink=NullSink(), ctx=_visual_ctx(tmp_path, "00-s")),
            "web": TargetRuntime(
                driver=web, sink=NullSink(), ctx=_visual_ctx(tmp_path, "00-s/web")
            ),
        },
        primary_target="app",
    )
    # Neither baseline exists yet, so both `visual` entries fail on the missing-baseline path —
    # not on a crash — which is what proves each target's own screenshot was actually captured.
    assert not r.ok
    assert r.failure is not None
    assert "crash" not in r.failure.lower()
    assert ("screenshot", str(tmp_path / "00-s" / "visual-actual.png")) in app.actions
    assert ("screenshot", str(tmp_path / "00-s" / "web" / "visual-actual.png")) in web.actions


def test_a_step_naming_an_unbuilt_target_fails_loudly_rather_than_silently_misrouting() -> None:
    # `step.target` is a declared field the load-time validator already checked against
    # `scenario.targets`, so a name absent from the live `by_target` map is a wiring defect — the
    # caller never built a runtime for a target the scenario declares — not an authoring mistake.
    # Falling through to the current driver would silently run the step against the wrong device
    # (prime directive 2: an ambiguous routing decision must fail, not guess).
    app = FakeDriver(screen=list(_APP_SCREEN))
    web = FakeDriver(screen=list(_WEB_SCREEN))
    scenario = _scenario(
        {
            "name": "orphaned-target",
            "targets": ["app", "web"],
            "steps": [
                {"target": "app", "tap": {"id": "app.button"}},
                {"target": "web", "tap": {"id": "web.button"}},
            ],
        }
    )
    # Only "app" gets a live runtime, standing in for a caller that forgot to build one for "web"
    # even though the scenario declares it.
    with pytest.raises(RuntimeError, match="no live runtime"):
        run_scenario(
            app,
            scenario,
            FakeClock(),
            target_runtimes={"app": TargetRuntime(driver=app, sink=NullSink())},
            primary_target="app",
        )
    assert _taps(web) == []  # never reached — the failure is loud, not a silent misroute
