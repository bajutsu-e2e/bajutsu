"""Tests for `before` / `after` — the scenario's own lifecycle phases (BE-0392).

`before` runs ahead of `steps` as its own phase and gates them; `after` runs once a verdict exists,
dispatching each rule on the run's own machine-checked outcome. Both reuse the ordinary step loop,
so the whole feature is covered here with a `FakeDriver` and no Simulator. Nothing an LLM decides
enters any of it: `on` is compared against the `failure` value the deterministic loop already
computed (prime directive 1).
"""

from __future__ import annotations

import pytest
from _orch import FakeClock, _scenario
from conftest import el
from pydantic import ValidationError

from bajutsu.capability_preflight import unsupported
from bajutsu.common.cancellation import RunCancelled, cancelled_teardown_seconds, grace_seconds
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.evidence_rules import requested_intervals
from bajutsu.common.runner.pipeline import with_lifecycle_phases
from bajutsu.config import load_config, resolve
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.scenario import (
    AfterRule,
    Component,
    Scenario,
    dump_scenarios,
    expand_components,
    load_scenarios,
)

_SCREEN = [el("a"), el("b"), el("c"), el("gone", label="gone")]


def _driver() -> FakeDriver:
    return FakeDriver(screen=list(_SCREEN))


def _run(scenario: Scenario, **kw: object) -> object:
    return run_scenario(_driver(), scenario, FakeClock(), **kw)  # type: ignore[arg-type]


# --- schema (Unit 1) -------------------------------------------------------------------------


def test_scenario_parses_both_phases_and_round_trips() -> None:
    text = """
- name: lifecycle
  before:
    - tap: { id: a }
  steps:
    - tap: { id: b }
  after:
    - on: always
      steps: [{ tap: { id: c } }]
    - on: error
      steps: [{ tap: { id: a } }]
"""
    s = load_scenarios(text)[0]
    assert [st.tap.id for st in s.before if st.tap] == ["a"]
    assert [r.on for r in s.after] == ["always", "error"]
    # `on` survives a dump: YAML 1.1 would resolve the bare key to True, so the dump quotes it and
    # `_yaml`'s restricted bool resolver reads it back as the string — the same round trip
    # `capturePolicy`'s own `on:` trigger already relies on.
    assert load_scenarios(dump_scenarios([s]))[0].after[0].on == "always"


def test_unset_phases_prune_from_a_dump() -> None:
    s = load_scenarios("- name: bare\n  steps: [{ tap: { id: a } }]\n")[0]
    dumped = dump_scenarios([s])
    assert "before:" not in dumped
    assert "after:" not in dumped


def test_unknown_outcome_word_is_rejected() -> None:
    # An outcome the runner has no dispatch for must fail at load time, not silently never fire.
    with pytest.raises(ValidationError):
        AfterRule.model_validate({"on": "failure", "steps": []})


def test_target_config_carries_both_phases() -> None:
    cfg = load_config(
        """
targets:
  app:
    bundleId: com.example.app
    before:
      - tap: { id: a }
    after:
      - on: always
        steps: [{ tap: { id: c } }]
"""
    )
    eff = resolve(cfg, "app")
    assert len(eff.run_defaults.before) == 1
    assert [r.on for r in eff.run_defaults.after] == ["always"]


# --- runner integration (Unit 3) -------------------------------------------------------------


def test_before_runs_first_and_reports_as_its_own_phase() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "before": [{"tap": {"id": "a"}}],
                "steps": [{"tap": {"id": "b"}}],
            }
        ),
        FakeClock(),
    )
    assert r.ok
    # Its own block, numbered from zero — not spliced into the scenario's own sequence the way a
    # `preconditions.setup` prelude is.
    assert [o.index for o in r.before_outcomes] == [0]
    assert [o.index for o in r.steps] == [0]


def test_a_failing_before_skips_steps_and_expect() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "before": [{"tap": {"id": "missing"}}],
                "steps": [{"tap": {"id": "b"}}],
                "expect": [{"exists": {"id": "b"}}],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    assert r.failure is not None and r.failure.startswith("before: ")
    assert r.steps == []
    assert r.expect_results == []


def test_vars_flow_from_before_into_steps_and_on_into_after() -> None:
    # One `live_bindings` dict across all three phases: `before` seeds a var `steps` addresses, and
    # `after` addresses one `steps` captured — the sharing a cleanup step needs to delete the very
    # record the run created.
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "before": [
                    {
                        "tap": {"id": "a"},
                        "extract": {"first": {"sel": {"id": "a"}, "prop": "identifier"}},
                    }
                ],
                "steps": [
                    {
                        "assert": [{"exists": {"id": "${vars.first}"}}],
                        "extract": {"second": {"sel": {"id": "b"}, "prop": "identifier"}},
                    }
                ],
                "after": [
                    {
                        "on": "always",
                        "steps": [{"assert": [{"exists": {"id": "${vars.second}"}}]}],
                    }
                ],
            }
        ),
        FakeClock(),
    )
    assert r.ok, r.failure
    assert r.after_outcomes[0].ok


def test_after_runs_even_when_a_step_failed() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "missing"}}],
                "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    # The gap this item exists to close: an identical trailing step would never have run, because
    # the step loop already broke.
    assert len(r.after_outcomes) == 1 and r.after_outcomes[0].ok


def test_outcome_dispatch_picks_only_the_matching_rules() -> None:
    rules = [
        {"on": "always", "steps": [{"tap": {"id": "a"}}]},
        {"on": "success", "steps": [{"tap": {"id": "b"}}]},
        {"on": "error", "steps": [{"tap": {"id": "c"}}]},
    ]
    passing = run_scenario(
        _driver(),
        _scenario({"name": "s", "steps": [{"tap": {"id": "a"}}], "after": rules}),
        FakeClock(),
    )
    failing = run_scenario(
        _driver(),
        _scenario({"name": "s", "steps": [{"tap": {"id": "missing"}}], "after": rules}),
        FakeClock(),
    )
    assert [o.action for o in passing.after_outcomes] == ["tap", "tap"]
    assert [o.action for o in failing.after_outcomes] == ["tap", "tap"]
    assert passing.ok and not failing.ok


def test_a_failing_before_dispatches_after_as_error() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "before": [{"tap": {"id": "missing"}}],
                "steps": [{"tap": {"id": "b"}}],
                "after": [
                    {"on": "success", "steps": [{"tap": {"id": "a"}}]},
                    {"on": "error", "steps": [{"tap": {"id": "c"}}]},
                ],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    assert len(r.after_outcomes) == 1  # the `error` rule alone


def test_a_success_rule_failure_becomes_the_sole_failure() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [{"on": "success", "steps": [{"tap": {"id": "missing"}}]}],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    assert r.failure is not None and r.failure.startswith("after: ")


def test_an_error_rule_failure_is_appended_behind_the_original_cause() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "missing"}}],
                "after": [{"on": "error", "steps": [{"tap": {"id": "also-missing"}}]}],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    assert r.failure is not None
    # The reason a reader sees first is the original cause, not the cleanup it triggered.
    assert r.failure.startswith("step 0 (tap)")
    assert "; after: " in r.failure


def test_a_failing_rule_does_not_skip_the_remaining_cleanup() -> None:
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [
                    {"on": "always", "steps": [{"tap": {"id": "missing"}}]},
                    {"on": "always", "steps": [{"tap": {"id": "c"}}]},
                ],
            }
        ),
        FakeClock(),
    )
    assert not r.ok
    assert [o.ok for o in r.after_outcomes] == [False, True]


# --- the two merge orders (Unit 2) ----------------------------------------------------------


_MERGE_CONFIG = """
targets:
  app:
    bundleId: com.example.app
    before:
      - tap: { id: a }
    after:
      - on: always
        steps: [{ tap: { id: b } }]
"""


def _merged(scenario: dict[str, object]) -> Scenario:
    eff = resolve(load_config(_MERGE_CONFIG), "app")
    return with_lifecycle_phases(eff, [_scenario({"name": "s", **scenario})])[0]


def test_before_merges_config_first_then_the_scenarios_own() -> None:
    # The app-wide prelude seeds the state this scenario's own setup builds on, so it runs first —
    # the config-then-scenario order `interrupts` already follows.
    merged = _merged({"steps": [{"tap": {"id": "a"}}], "before": [{"tap": {"id": "c"}}]})
    assert [s.tap.id for s in merged.before if s.tap] == ["a", "c"]


def test_after_merges_the_scenarios_own_first_then_config() -> None:
    # The reverse order: this scenario releases what it created before the app-wide teardown closes
    # around it, the last-acquired-first-released order a fixture teardown pair gives.
    merged = _merged(
        {
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
        }
    )
    assert [r.steps[0].tap.id for r in merged.after if r.steps[0].tap] == ["c", "b"]


def test_a_config_with_no_phases_leaves_the_scenarios_untouched() -> None:
    eff = resolve(load_config("targets:\n  app:\n    bundleId: com.example.app\n"), "app")
    scenarios = [_scenario({"name": "s", "steps": [{"tap": {"id": "a"}}]})]
    assert with_lifecycle_phases(eff, scenarios) is scenarios


def test_the_merged_phases_are_what_the_run_executes() -> None:
    merged = _merged({"steps": [{"tap": {"id": "a"}}], "before": [{"tap": {"id": "c"}}]})
    r = run_scenario(_driver(), merged, FakeClock())
    assert r.ok, r.failure
    assert len(r.before_outcomes) == 2  # the app-wide step and this scenario's own
    assert len(r.after_outcomes) == 1  # the app-wide teardown


def test_each_dispatched_rule_gets_its_own_step_numbering() -> None:
    # The `after` phase runs one step-loop call per rule. Restarting the numbering per rule would
    # give two rules' first steps the same evidence `step_id`, so the second would overwrite the
    # first's screenshots and the JUnit body would print two lines both labeled `after step 0`.
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [
                    {"on": "always", "steps": [{"tap": {"id": "b"}}]},
                    {"on": "success", "steps": [{"tap": {"id": "c"}}]},
                ],
            }
        ),
        FakeClock(),
    )
    assert [o.index for o in r.after_outcomes] == [0, 1]


# --- cancellation (Unit 3) -------------------------------------------------------------------


def test_a_cancelled_run_still_runs_its_cleanup() -> None:
    # The latched source: once set it never clears, so reading it inside the phase would abandon
    # every entry before the first one ran.
    r = run_scenario(
        _driver(),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
            }
        ),
        FakeClock(),
        cancelled=lambda: True,
    )
    assert not r.ok
    assert r.failure is not None and r.failure.startswith("cancelled")
    assert len(r.after_outcomes) == 1 and r.after_outcomes[0].ok


def test_a_cancelled_runs_cleanup_is_abandoned_once_its_budget_is_spent() -> None:
    clock = FakeClock()
    budget = cancelled_teardown_seconds(grace_seconds())

    def burn(_driver: FakeDriver, _action: str, _payload: object) -> None:
        clock.sleep(budget)  # the first entry alone spends the whole window

    r = run_scenario(
        FakeDriver(screen=list(_SCREEN), react=burn),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [
                    {"on": "always", "steps": [{"tap": {"id": "b"}}]},
                    {"on": "always", "steps": [{"tap": {"id": "c"}}]},
                ],
            }
        ),
        clock,
        cancelled=lambda: True,
    )
    assert len(r.after_outcomes) == 1  # the second entry never started
    # Abandoning the rest is the designed bound, not a second failure to report.
    assert r.failure == "cancelled"


def test_a_cancel_arriving_during_teardown_fails_the_passing_run_as_cancelled() -> None:
    # The genuine mid-teardown case: the run reached the phase uncancelled, so the phase reads the
    # live source the way any step does, and the cancel that lands inside it fails the run.
    taps = 0

    def arm(_driver: FakeDriver, action: str, _payload: object) -> None:
        nonlocal taps
        if action == "tap":
            taps += 1

    r = run_scenario(
        FakeDriver(screen=list(_SCREEN), react=arm),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [
                    {"on": "always", "steps": [{"tap": {"id": "b"}}, {"tap": {"id": "c"}}]},
                ],
            }
        ),
        FakeClock(),
        cancelled=lambda: taps >= 2,  # latches once the phase's first step has acted
    )
    assert r.failure == "cancelled"
    assert len(r.after_outcomes) == 1  # the second teardown step never started


def test_a_cancel_latching_after_the_last_step_still_lets_cleanup_run() -> None:
    # The window the deadline exists for: the cancel lands after the last step's boundary check, so
    # `steps` finishes and the run keeps its real verdict (BE-0370). The phase is still bounded by
    # its own deadline rather than by the latched source, which would abandon every rule unread.
    stop = False

    def arm(_driver: FakeDriver, _action: str, _payload: object) -> None:
        nonlocal stop
        stop = True

    r = run_scenario(
        FakeDriver(screen=list(_SCREEN), react=arm),
        _scenario(
            {
                "name": "s",
                "steps": [{"tap": {"id": "a"}}],
                "after": [{"on": "success", "steps": [{"tap": {"id": "c"}}]}],
            }
        ),
        FakeClock(),
        cancelled=lambda: stop,
    )
    assert r.ok, r.failure
    assert r.after_verdict == "success"
    assert len(r.after_outcomes) == 1


def test_run_cancelled_is_never_raised_out_of_the_after_phase() -> None:
    try:
        run_scenario(
            _driver(),
            _scenario(
                {
                    "name": "s",
                    "steps": [{"tap": {"id": "a"}}],
                    "after": [{"on": "always", "steps": [{"tap": {"id": "c"}}]}],
                }
            ),
            FakeClock(),
            cancelled=lambda: True,
        )
    except RunCancelled:  # pragma: no cover - the assertion is that we never get here
        raise AssertionError("the after phase must absorb its own cancellation") from None


# --- components inside a phase (Unit 1 / Unit 3) ----------------------------------------------


def test_a_use_step_inside_either_phase_is_expanded() -> None:
    # `use` is part of the ordinary step grammar the phases reuse. Left unexpanded it reaches the
    # step loop as a step with no action and aborts the whole run with an `AssertionError` — not one
    # failed scenario — so the expansion must cover the phases as well as `steps`.
    scenario = _scenario(
        {
            "name": "s",
            "before": [{"use": {"component": "c"}}],
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "always", "steps": [{"use": {"component": "c"}}]}],
        }
    )
    expand_components(
        [scenario], lambda _ref: Component.model_validate({"steps": [{"tap": {"id": "b"}}]})
    )
    assert scenario.before[0].use is None
    assert scenario.after[0].steps[0].use is None
    r = run_scenario(_driver(), scenario, FakeClock())
    assert r.ok, r.failure
    assert [o.action for o in r.before_outcomes] == ["tap"]
    assert [o.action for o in r.after_outcomes] == ["tap"]


def test_an_app_wide_phase_cannot_use_a_component() -> None:
    # `use` is expanded per scenario file, which a target config never passes through: an app-wide
    # one would reach the step loop with no action and abort the whole run rather than fail one
    # scenario. Rejected at load, loudly, instead of degrading mid-run.
    with pytest.raises(ValidationError, match="cannot use a component"):
        load_config(
            "targets:\n"
            "  app:\n"
            "    bundleId: com.example.app\n"
            "    after:\n"
            "      - on: always\n"
            "        steps: [{ use: { component: c } }]\n"
        )


def test_the_capability_preflight_sees_a_hook_steps_construct() -> None:
    # Missed here, an unsupported teardown step passes the preflight, a device is leased, and the
    # whole scenario is driven before the step fails — and a failing `after` rule on an otherwise
    # passing run becomes that run's failure, so a green scenario reports red after full device work.
    scenario = _scenario(
        {
            "name": "s",
            "before": [{"pinch": {"sel": {"id": "a"}, "scale": 2.0}}],
            "steps": [{"tap": {"id": "a"}}],
            "after": [
                {
                    "on": "always",
                    "steps": [{"selectOption": {"sel": {"id": "a"}, "option": "y"}}],
                }
            ],
        }
    )
    reasons = unsupported(scenario, {base.Capability.QUERY, base.Capability.ELEMENTS})
    assert any("before" in r for r in reasons), reasons
    assert any("after[0]" in r for r in reasons), reasons


def test_an_interval_capture_on_a_hook_step_opens_its_interval() -> None:
    # An interval kind is opened once for the whole scenario and split back out of the per-step list
    # downstream, so a hook step naming one and not seen here records nothing — and appends no
    # `SkippedCapture` either, leaving the gap undisclosed (the opposite of BE-0020).
    scenario = _scenario(
        {
            "name": "s",
            "before": [{"tap": {"id": "a"}, "capture": ["video"]}],
            "steps": [{"tap": {"id": "a"}}],
            "after": [{"on": "always", "steps": [{"tap": {"id": "c"}, "capture": ["deviceLog"]}]}],
        }
    )
    assert set(requested_intervals(scenario)) == {"video", "deviceLog"}
