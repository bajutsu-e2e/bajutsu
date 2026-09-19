"""The step loop's reactive app-crash check and the three latches bounding it (BE-0424).

The classification is deliberately in-band: `AppCrashedError` never leaves `_finish_outcome`, so a
scenario whose app crashed finishes through the same `RunResult` assembly as any other terminal step
failure. These tests pin that, and — more importantly — pin the three latches that decide *when* a
driver is asked at all. Each exists to stop a specific misdiagnosis, and each would fail silently if
it regressed: the probe simply would not run, or would run and confirm something the run itself
caused.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import RunResult, run_scenario
from bajutsu.common.orchestrator.types import StepOutcome
from bajutsu.common.platform_lifecycle.protocols import ReadinessResult
from bajutsu.common.runner.pipeline import _launch_unconfirmed

_SIGNAL = "the app under test is no longer running"


class CrashingDriver(FakeDriver):
    """A `FakeDriver` that also implements `base.AppCrashSignal`, counting every probe.

    The count is the assertion surface for the latches: "the probe never ran" and "the probe ran and
    answered None" are different outcomes that an `app_crashed` check alone cannot tell apart.
    """

    def __init__(
        self,
        elements: list[base.Element],
        *,
        signal: str | None = _SIGNAL,
        react: Callable[[FakeDriver, str, object], None] | None = None,
    ) -> None:
        super().__init__(elements, react=react)
        self.signal = signal
        self.probes = 0

    def app_crash_signal(self) -> str | None:
        self.probes += 1
        return self.signal


def _outcomes(result: RunResult) -> list[StepOutcome]:
    return [*result.before_outcomes, *result.steps, *result.after_outcomes]


def _crashed(result: RunResult) -> list[StepOutcome]:
    return [o for o in _outcomes(result) if o.app_crashed]


def _seen(calls: list[int], value: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
    calls.append(1)
    return value


def _run(driver: FakeDriver, steps: list[dict[str, object]], **kw: Any) -> RunResult:
    return run_scenario(
        driver,
        _scenario({"name": "app crash", "steps": steps}),
        clock=FakeClock(),
        **kw,
    )


# --- the classification itself -------------------------------------------------------------------


def test_a_confirmed_crash_fails_the_step_in_band_and_names_the_event() -> None:
    # In-band is the whole design: the scenario comes back as an ordinary failed `RunResult`, not as
    # an escaping exception the pipeline would have to build a terminal result from.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(driver, [{"tap": {"id": "gone"}}])

    assert not result.ok
    assert _SIGNAL in (result.failure or "")
    crashed = _crashed(result)
    assert len(crashed) == 1
    assert crashed[0].action == "tap"


def test_the_failing_step_keeps_its_own_reason_alongside_the_crash_note() -> None:
    # The selector that could not be found is still the concrete thing the step was doing when the
    # app went down, so it is appended to rather than replaced — the same shape an undeclared
    # interruption's note uses.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(driver, [{"tap": {"id": "gone"}}])

    reason = _crashed(result)[0].reason
    assert "gone" in reason
    assert _SIGNAL in reason


def test_an_ordinary_failure_with_no_crash_behind_it_is_probed_but_not_classified() -> None:
    # The "cannot confirm" answer must stay distinguishable from "never asked": the driver *is*
    # asked here, and the scenario still fails as the ordinary `ElementNotFound` it is.
    driver = CrashingDriver([el("home", "Home")], signal=None)
    result = _run(driver, [{"tap": {"id": "gone"}}])

    assert not result.ok
    assert driver.probes == 1
    assert _crashed(result) == []


def test_a_green_scenario_never_probes_at_all() -> None:
    # The reason the check is reactive rather than polled: a green run must pay nothing for a failure
    # mode that is rare by construction.
    driver = CrashingDriver([el("ok", "OK", ["button"])])
    result = _run(driver, [{"tap": {"id": "ok"}}])

    assert result.ok, result.failure
    assert driver.probes == 0


def test_a_driver_without_the_capability_is_never_asked() -> None:
    # `PlaywrightDriver` and the fake backend take this path: `isinstance` answers False and the run
    # is otherwise unchanged, which is what keeps this a narrow opt-in rather than a `Driver` member.
    driver = FakeDriver([el("home", "Home")])
    result = _run(driver, [{"tap": {"id": "gone"}}])

    assert not result.ok
    assert not any(o.app_crashed for o in result.steps)


# --- latch 1: a deliberate termination ------------------------------------------------------------


def test_a_failing_relaunch_is_never_probed() -> None:
    # `relaunch` is the one step that deliberately terminates the app, so reading its own failure as
    # a crash would report the check's own scenario back as a defect.
    driver = CrashingDriver([el("home", "Home")])

    def relaunch(_step: object) -> None:
        # `UnsupportedAction` is one of the four the step body folds into `ok=False`; a bare
        # `RuntimeError` would escape `run_scenario` entirely and never reach the check at all.
        raise base.UnsupportedAction("relaunch failed")

    result = _run(driver, [{"relaunch": {}}], relaunch=relaunch)

    assert not result.ok
    assert driver.probes == 0
    assert _crashed(result) == []


def test_a_failed_relaunch_suppresses_every_later_probe_in_the_scenario() -> None:
    # The latch has to be scenario-scoped, not per-outcome: the wrapping outcomes and any `after`
    # step carry a different `outcome.action` and would each otherwise probe fresh, reading the
    # app's honest `notRunning` as a crash the failed `relaunch` itself caused.
    driver = CrashingDriver([el("home", "Home")])

    def relaunch(_step: object) -> None:
        raise base.UnsupportedAction("relaunch failed")

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "relaunch then cleanup",
                "steps": [{"relaunch": {}}],
                "after": [{"on": "error", "steps": [{"tap": {"id": "gone"}}]}],
            }
        ),
        clock=FakeClock(),
        relaunch=relaunch,
    )

    assert not result.ok
    assert driver.probes == 0
    assert _crashed(result) == []


# --- latch 2: an unconfirmed launch ---------------------------------------------------------------


def test_an_unconfirmed_launch_suppresses_the_first_failing_step() -> None:
    # A scenario's very first failing step has no earlier step to have observed the app running, so
    # an app that never reached the foreground would otherwise read as one that crashed.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(driver, [{"tap": {"id": "gone"}}], app_launch_unconfirmed=True)

    assert not result.ok
    assert driver.probes == 0
    assert _crashed(result) == []


def test_a_successful_step_clears_the_unconfirmed_launch_latch() -> None:
    # Unlike the relaunch latch, this one clears: latching it for a whole scenario would disable the
    # check outright for every target whose readiness lands on the bare-count rung.
    driver = CrashingDriver([el("ok", "OK", ["button"])])
    result = _run(
        driver,
        [{"tap": {"id": "ok"}}, {"tap": {"id": "gone"}}],
        app_launch_unconfirmed=True,
    )

    assert not result.ok
    assert driver.probes == 1
    assert len(_crashed(result)) == 1


def test_a_non_matching_if_does_not_clear_the_unconfirmed_launch_latch() -> None:
    # The reason the clear is keyed positively: `_run_if` settles `ok=True` the instant its condition
    # fails to match, without the app behind the query ever answering — a dead app's SpringBoard-only
    # tree answers a `query()` perfectly well, so this would clear the latch for the exact reason it
    # should not.
    driver = CrashingDriver([el("flag", "F", value="off")])
    result = _run(
        driver,
        [
            {
                "if": {
                    "condition": {"value": {"sel": {"id": "flag"}, "equals": "on"}},
                    "then": [{"tap": {"id": "ok"}}],
                }
            },
            {"tap": {"id": "gone"}},
        ],
        app_launch_unconfirmed=True,
    )

    assert not result.ok
    assert driver.probes == 0


def test_a_step_that_never_reaches_the_app_does_not_clear_the_latch() -> None:
    # `generate` produces a value host-side and never touches the device, so its success is no
    # evidence the app is up.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(
        driver,
        [
            {"generate": {"random": {"uuid": {}}, "into": {"var": "token"}}},
            {"tap": {"id": "gone"}},
        ],
        app_launch_unconfirmed=True,
    )

    assert not result.ok
    assert driver.probes == 0


def test_a_settled_relaunch_re_arms_the_unconfirmed_launch_latch() -> None:
    # `relaunch`'s own closure discards `await_ready`'s result and `await_ready` never raises, so a
    # `relaunch` reporting success says nothing about whether the app it just launched came up — it
    # puts the scenario back into exactly the unconfirmed state a fresh launch leaves it in.
    driver = CrashingDriver([el("ok", "OK", ["button"])])
    result = _run(
        driver,
        [{"tap": {"id": "ok"}}, {"relaunch": {}}, {"tap": {"id": "gone"}}],
        relaunch=lambda _step: None,
    )

    assert not result.ok
    assert driver.probes == 0


def test_launch_unconfirmed_reads_the_readiness_rung() -> None:
    # `count` is the gate's weakest rung — a bare element count a slow cold boot's SpringBoard icons
    # satisfy before the app foregrounds — and it is the *ordinary* rung for any target declaring no
    # `readyWhen`, which is why it cannot be treated as evidence the app arrived.
    assert _launch_unconfirmed(None) is True
    assert (
        _launch_unconfirmed(ReadinessResult(ready=False, signal="timeout", elapsed_s=1.0)) is True
    )
    assert _launch_unconfirmed(ReadinessResult(ready=True, signal="count", elapsed_s=1.0)) is True
    assert (
        _launch_unconfirmed(ReadinessResult(ready=True, signal="readyWhen", elapsed_s=1.0)) is False
    )


# --- latch 3: a confirmed crash -------------------------------------------------------------------


def test_a_nested_crash_marks_exactly_one_outcome_and_probes_once() -> None:
    # A crash inside a `forEach` inside an `if` settles three wrapping outcomes in turn. Only the
    # first, confirming one may carry `app_crashed` — otherwise `pipeline.py`'s later scan would have
    # to choose among several — and the driver must be asked exactly once.
    driver = CrashingDriver([el("home", "Home"), el("row", "Row")])
    result = _run(
        driver,
        [
            {
                "if": {
                    "condition": {"exists": {"sel": {"id": "home"}}},
                    "then": [
                        {
                            "forEach": {
                                "sel": {"id": "row"},
                                "as": "r",
                                "steps": [{"tap": {"id": "gone"}}],
                            }
                        }
                    ],
                }
            }
        ],
    )

    assert not result.ok
    assert driver.probes == 1
    assert len(_crashed(result)) == 1
    assert _crashed(result)[0].action == "tap"
    # Two levels of nesting, so the note must still land once: the wrapping outcomes inherit the
    # inner step's reason, and folding the signal in unconditionally produced one copy per level.
    outer = [o for o in result.steps if o.action == "if_"]
    assert len(outer) == 1
    assert outer[0].reason.count(_SIGNAL) == 1


def test_the_wrapping_outcomes_still_carry_the_signal_in_their_reason() -> None:
    # The latch suppresses the *probe* and the second classification, not the message: a contributor
    # reading any of the settled outcomes should still see what happened.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(
        driver,
        [
            {
                "if": {
                    "condition": {"exists": {"sel": {"id": "home"}}},
                    "then": [{"tap": {"id": "gone"}}],
                }
            }
        ],
    )

    wrapping = [o for o in result.steps if o.action == "if_"]
    assert len(wrapping) == 1
    assert wrapping[0].reason.count(_SIGNAL) == 1
    assert wrapping[0].app_crashed is False


def test_an_unconfirmed_answer_is_not_latched() -> None:
    # Deliberately asymmetric: a `None` for one step teaches nothing about whether the *next* step's
    # own failure is a crash, so latching there would risk missing a real one.
    driver = CrashingDriver([el("ok", "OK", ["button"])], signal=None)
    result = _run(
        driver,
        [{"tap": {"id": "missing-one"}}],
    )
    assert not result.ok
    # One probe for the failing action, one for nothing else — but crucially the latch did not set,
    # which the next scenario-level assertion below exercises through a second failing step.
    assert driver.probes == 1

    driver2 = CrashingDriver([el("home", "Home"), el("row", "Row")], signal=None)
    _run(
        driver2,
        [
            {
                "if": {
                    "condition": {"exists": {"sel": {"id": "home"}}},
                    "then": [{"tap": {"id": "gone"}}],
                }
            }
        ],
    )
    # The action's own outcome and the wrapping `if`'s both probe: nothing was latched by the first
    # unconfirmed answer.
    assert driver2.probes == 2


# --- the probe's own failure must not abort the run -------------------------------------------------


class _RaisingCrashProbe(FakeDriver):
    """A `FakeDriver` whose `app_crash_signal()` raises instead of answering (BE-0424)."""

    def __init__(self, elements: list[base.Element], *, raises: BaseException) -> None:
        super().__init__(elements)
        self._raises = raises
        self.probes = 0

    def app_crash_signal(self) -> str | None:
        self.probes += 1
        raise self._raises


def test_a_probe_failure_that_is_not_a_backend_crash_is_swallowed() -> None:
    # The probe runs only after a step has already failed; anything it raises besides
    # `BackendCrashError` must not also take down the scenario's own result — `run_scenario` converts
    # only `ControlChannelError` / `RunCancelled`, so an escaping `RuntimeError` here would discard
    # every scenario's result, not just this one's (the failure mode a truncated `/app/state` reply
    # from `XcuitestDriver.app_crash_signal` produces in practice).
    driver = _RaisingCrashProbe([el("home", "Home")], raises=RuntimeError("truncated reply"))
    result = _run(driver, [{"tap": {"id": "gone"}}])

    assert not result.ok
    assert driver.probes == 1
    assert _crashed(result) == []


def test_a_backend_crash_from_the_probe_still_propagates() -> None:
    # The one exception this catch must not swallow: a dead backend already belongs to the recovery
    # path that owns `BackendCrashError`, so re-raising it here is what keeps that path in charge
    # rather than misreporting the dead channel as an ordinary step failure.
    driver = _RaisingCrashProbe([el("home", "Home")], raises=base.BackendCrashError("gone"))

    with pytest.raises(base.BackendCrashError):
        _run(driver, [{"tap": {"id": "gone"}}])


# --- the capture -----------------------------------------------------------------------------------


def test_the_capture_runs_at_confirmation_and_lands_on_the_outcome() -> None:
    # Captured synchronously at confirmation rather than after the scenario finishes: a teardown
    # `relaunch` in the same `after` phase would otherwise re-stamp the launch marker the sweep
    # matches against, and a later read would sweep past the crash it exists to attribute.
    driver = CrashingDriver([el("home", "Home")])
    calls: list[int] = []

    def capture() -> list[tuple[str, bytes]]:
        calls.append(1)
        return [("Showcase.ips", b"the report")]

    result = _run(driver, [{"tap": {"id": "gone"}}], capture_app_crash=capture)

    assert len(calls) == 1
    assert _crashed(result)[0].app_crash_artifacts == (("Showcase.ips", b"the report"),)


def test_a_teardown_after_the_crash_does_not_re_capture() -> None:
    # The regression this pins: the capture must happen once, at confirmation, before the `after`
    # phase runs — not once per settling outcome, and not again from the teardown step.
    driver = CrashingDriver([el("home", "Home")])
    calls: list[int] = []

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "crash then cleanup",
                "steps": [{"tap": {"id": "gone"}}],
                "after": [{"on": "error", "steps": [{"tap": {"id": "also-gone"}}]}],
            }
        ),
        clock=FakeClock(),
        capture_app_crash=lambda: _seen(calls, [("r.ips", b"x")]),
    )

    assert not result.ok
    assert len(calls) == 1
    assert len(_crashed(result)) == 1


def test_no_capture_callable_still_classifies() -> None:
    # A caller with no lease behind it (a test, a `record` path) must see the classification without
    # needing a sweep to exist at all.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(driver, [{"tap": {"id": "gone"}}])

    assert _crashed(result)[0].app_crash_artifacts == ()


# --- the documented blind spots -------------------------------------------------------------------


def test_a_crash_on_the_last_step_stays_green_when_nothing_follows_it() -> None:
    # Pinned rather than left to be rediscovered as a bug. The trigger's own actuation succeeds — the
    # tap lands before the app dies — so it is never probed, and with no `expect` and no non-failure
    # `after` rule there is nothing after it to fail. Closing this would need an unconditional
    # end-of-scenario probe, the every-green-run cost this design exists to avoid.
    driver = CrashingDriver([el("ok", "OK", ["button"])])
    result = _run(driver, [{"tap": {"id": "ok"}}])

    assert result.ok, result.failure
    assert driver.probes == 0
    assert _crashed(result) == []


def test_a_crash_on_the_last_step_with_a_scenario_expect_goes_red_unclassified() -> None:
    # A second, distinct blind spot from the green one above, not a variant of it: `_evaluate_expect`
    # produces `AssertionResult`s, never `StepOutcome`s, so it never reaches `_finish_outcome` at all.
    # A reader sees an ordinary assertion mismatch, not the crash that actually caused it.
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap":
            d.screen = []  # the app is gone; nothing is left to query

    driver = CrashingDriver([el("ok", "OK", ["button"])], react=react)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "crash on the last step, scenario expect",
                "steps": [{"tap": {"id": "ok"}}],
                "expect": [{"exists": {"sel": {"id": "ok"}}}],
            }
        ),
        clock=FakeClock(),
    )

    assert not result.ok
    assert driver.probes == 0
    assert _crashed(result) == []
    assert result.expect_results and not result.expect_results[0].ok


def test_an_after_rule_that_runs_on_success_catches_the_crash_one_step_later() -> None:
    # The other half of the same shape, and a genuinely different outcome: a teardown step that runs
    # anyway *does* reach the check through the ordinary `run_phase` path, so the crash is caught one
    # step later rather than missed.
    driver = CrashingDriver([el("ok", "OK", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "crash on the last step, teardown after",
                "steps": [{"tap": {"id": "ok"}}],
                "after": [{"on": "always", "steps": [{"tap": {"id": "gone"}}]}],
            }
        ),
        clock=FakeClock(),
    )

    assert not result.ok
    assert len(_crashed(result)) == 1
    assert _crashed(result)[0].action == "tap"


# --- every step kind settles through the shared helper ---------------------------------------------


def test_every_step_kind_settles_through_the_shared_check() -> None:
    # A behavioural pin, not a grep for `self.state.outcomes.append`: a sixth settle point added
    # later — including one spelled `insert` / `extend` / `+=`, or reached through a local alias —
    # must not reopen the gap silently. Each kind below fails its own way and must be probed.
    for steps, expected_action in (
        ([{"tap": {"id": "gone"}}], "tap"),
        ([{"wait": {"for": {"id": "gone"}, "timeout": 0}}], "wait"),
        ([{"assert": [{"exists": {"sel": {"id": "gone"}}}]}], "assert_"),
        (
            [{"if": {"condition": {"exists": {"sel": {"id": "gone"}}}, "then": []}}],
            None,  # a non-matching condition settles ok=True, so nothing is probed
        ),
    ):
        driver = CrashingDriver([el("home", "Home")])
        result = _run(driver, steps)  # type: ignore[arg-type]
        if expected_action is None:
            assert driver.probes == 0
            continue
        assert driver.probes >= 1, steps
        assert any(o.app_crashed and o.action == expected_action for o in _outcomes(result)), steps


def test_a_failing_inner_web_step_never_probes_but_the_wrapping_web_step_does() -> None:
    # `_handle_web`'s own settle passes the block's native `active_driver`, not the inner
    # `WebContextDriver` its own steps run against — deliberately, since a host crash surfacing as
    # the wrapping `web` step's own failure (the native app dying out from under the WebView bridge)
    # is not a no-op the way an ordinary DOM assertion failure is. The inner step's own outcome
    # settles through a `WebContextDriver`, which never satisfies `base.AppCrashSignal`, so it must
    # never probe; the wrapping outcome settles through the native driver and must.
    class _Bridge:
        def query_dom(self, webview_id: str) -> list[base.Element]:
            return []  # empty DOM: the inner tap's selector never resolves

        def tap_element(self, webview_id: str, point: tuple[float, float]) -> None:
            raise AssertionError("nothing to tap in an empty DOM")

        def type_text(self, webview_id: str, text: str) -> None:
            pass

        def scroll_to(self, webview_id: str, element_id: str) -> None:
            pass

    driver = CrashingDriver([el("checkout.webview", "WebView", frame=(0.0, 0.0, 400.0, 800.0))])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "web crash",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "checkout.webview"},
                            "steps": [{"tap": {"id": "missing-in-dom"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=_Bridge(),
    )

    assert not result.ok
    assert driver.probes == 1  # only the wrapping `web` outcome's settle probed
    web_outcomes = [o for o in result.steps if o.action == "web"]
    assert len(web_outcomes) == 1
    assert web_outcomes[0].app_crashed is True


def test_an_uncovered_system_alert_locale_still_settles_through_the_shared_check() -> None:
    # `_handle_action`'s `UncoveredSystemAlertLocale` early return is the one exit this file's own
    # comments already single out as the exit an earlier draft's shared-settle refactor missed —
    # named explicitly in Unit 7's own rationale, so it gets its own pin rather than relying on the
    # generic step-kind sweep above, which never reaches a `handleSystemAlert` step at all.
    driver = CrashingDriver([el("home", "Home")])
    result = _run(
        driver,
        [{"handleSystemAlert": {"prompt": "notifications", "choice": "grant", "timeout": 5}}],
        locale="de_DE",
    )

    assert not result.ok
    assert "language 'de'" in (result.failure or "")
    assert driver.probes == 1
    assert len(_crashed(result)) == 1
    assert _crashed(result)[0].action == "handle_system_alert"
