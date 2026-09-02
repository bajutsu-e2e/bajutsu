"""Tests for cooperative cancellation inside a scenario (BE-0370).

A cancelled run must stop at a boundary the pipeline already tolerates a pause at — between steps, or
on a condition wait's own poll — and come back as an ordinary failed scenario, so the run it belongs
to still writes a manifest, a report, and a history row instead of vanishing.
"""

from __future__ import annotations

import pytest
from _orch import FakeClock, _scenario
from conftest import AlertingDriver, el

from bajutsu.cancellation import CANCELLED_FAILURE
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence.network import ScreenTransition
from bajutsu.orchestrator import run_scenario
from bajutsu.orchestrator.types import AlertGuardConfig


class _CancelAfter:
    """A `CancelSource` reporting "not cancelled" for its first `after` reads, then cancelled.

    Counting reads (rather than wall time) is what pins *where* the run noticed: read 1 is the first
    step's boundary check, so `after=1` lands the cancel inside that step's own poll loop.
    """

    def __init__(self, after: int) -> None:
        self.after = after
        self.reads = 0

    def __call__(self) -> bool:
        self.reads += 1
        return self.reads > self.after


def test_cancel_between_steps_fails_the_scenario_as_cancelled() -> None:
    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}, {"tap": {"id": "b"}}]}),
        clock=FakeClock(),
        cancelled=_CancelAfter(1),  # read 1 = step 1's boundary, read 2 = step 2's
    )
    assert not result.ok
    assert result.failure == CANCELLED_FAILURE
    # The step that completed keeps its real outcome; the one the cancel arrived before never ran, so
    # it records nothing rather than a step the report would show as attempted.
    assert [(o.action, o.ok) for o in result.steps] == [("tap", True)]


def test_cancel_at_the_first_boundary_leaves_the_scenario_with_no_steps() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "a"}}]}),
        clock=FakeClock(),
        cancelled=_CancelAfter(0),
    )
    assert not result.ok
    assert result.failure == CANCELLED_FAILURE
    assert result.steps == []


def test_cancel_inside_a_for_wait_does_not_burn_its_timeout() -> None:
    # The whole point of checking inside the poll loop: a wait blocked on a condition would otherwise
    # hold the run for its full timeout, overrunning the grace window the canceller is waiting out.
    driver = FakeDriver([el("a", "A", ["button"])])
    clock = FakeClock()
    cancelled = _CancelAfter(1)  # read 1 = the step boundary, read 2 = the wait's first poll
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 100.0}}]}),
        clock=clock,
        cancelled=cancelled,
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0  # one polling tick, not the 100s timeout
    assert cancelled.reads == 2


def test_cancel_inside_a_settled_wait_ends_the_settle() -> None:
    # `settled` never fails a step on its own (it is a stabilization hint), so without its own check a
    # cancelled run would settle all the way to the deadline before the next boundary saw anything.
    driver = FakeDriver([el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        driver.screen = [el("a", "A", ["button"]), el(f"x{t}", "X")]  # never stops changing

    clock = FakeClock(on_sleep)
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 100.0}}]}),
        clock=clock,
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_cancel_inside_an_assert_poll_ends_the_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # A step-level `assert` is a condition wait too (BE-0299), and the wait floor gives it a real
    # budget on the lane that sets one — so it honors a cancel the same way `wait` does.
    monkeypatch.setenv("BAJUTSU_MIN_WAIT_TIMEOUT", "100")
    driver = FakeDriver([el("a", "A", ["button"])])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"assert": [{"exists": {"id": "never"}}]}]}),
        clock=clock,
        cancelled=_CancelAfter(1),
    )
    # The cancel wins over the assertion the poll would eventually have reported at its deadline: the
    # run stopped, so the scenario never reached a verdict on that assertion.
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_a_wait_satisfied_on_the_polling_cancel_still_passes() -> None:
    # The condition check precedes the cancel check in every branch, so a wait that is already
    # satisfied on the poll a cancel lands on is not turned into a failure retroactively.
    driver = FakeDriver([el("ready", "R", ["button"])])
    cancelled = _CancelAfter(1)
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(),
        cancelled=cancelled,
    )
    assert result.ok
    assert cancelled.reads == 1  # only the boundary read; the wait returned before its own check


def test_cancel_inside_an_email_poll_ends_the_poll() -> None:
    # `email.timeout` is whatever the scenario asked for, and a wait for a one-time password commonly
    # runs to a minute or more — long enough that a cancelled run stuck here would outlive the grace
    # window and be killed before it wrote its manifest.
    class _EmptyMailbox:
        def fetch(self, timeout: float) -> list[object]:
            return []

    driver = FakeDriver([el("a", "A", ["button"])])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {
                        "email": {
                            "match": {"subjectMatches": "Verify"},
                            "extract": {"var": "otp", "bodyMatches": r"PIN (\d+)"},
                            "timeout": 120.0,
                        }
                    }
                ],
            }
        ),
        clock=clock,
        mailbox=_EmptyMailbox(),  # type: ignore[arg-type]
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 5.0  # one polling tick, not the 120s timeout


def test_cancel_inside_a_gone_wait_does_not_burn_its_timeout() -> None:
    driver = FakeDriver([el("here", "H", ["button"])])  # the target never goes away
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [{"wait": {"until": {"gone": {"id": "here"}}, "timeout": 100.0}}],
            }
        ),
        clock=clock,
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_cancel_inside_a_request_wait_does_not_burn_its_timeout() -> None:
    # This branch polls the observed network rather than the screen, so it has its own loop.
    driver = FakeDriver([el("a", "A", ["button"])])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "x",
                "steps": [
                    {"wait": {"until": {"request": {"urlMatches": "/never"}}, "timeout": 100.0}}
                ],
            }
        ),
        clock=clock,
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_cancel_inside_a_screen_changed_wait_does_not_burn_its_timeout() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])  # a screen that never changes
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 100.0}}]}),
        clock=clock,
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_cancel_inside_a_signal_settled_wait_ends_the_settle() -> None:
    # The settle has two paths (BE-0310): the tree-diff fallback, and this one, taken the moment a
    # screen-transition report lands. Each runs its own loop, so each needs its own check.
    driver = FakeDriver([el("a", "A", ["button"])])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 100.0}}]}),
        clock=clock,
        # A transition reported after the scenario's start instant, which is what routes the settle
        # onto the signal path rather than the tree-diff fallback.
        transitions=lambda: [(ScreenTransition(kind="detail"), 0.0)],
        cancelled=_CancelAfter(1),
    )
    assert result.failure == CANCELLED_FAILURE
    assert clock.now() < 1.0


def test_a_settle_that_reached_its_deadline_still_passes_under_a_cancel() -> None:
    # A settle never fails a step: reaching its deadline is a *pass*. So the cancel check sits below
    # that return, or a settle which had already finished would be turned into a cancelled failure —
    # and a scenario whose every step passed must keep its real verdict.
    driver = FakeDriver([el("a", "A", ["button"])])
    # A zero budget puts the settle at its deadline on its very first poll, which is the moment the
    # ordering decides: the deadline return is a pass, so it has to win over the cancel check.
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 0.0}}]}),
        clock=FakeClock(),
        cancelled=_CancelAfter(1),
    )
    assert result.ok
    assert result.failure is None


def test_a_signal_settle_that_reached_its_deadline_still_passes_under_a_cancel() -> None:
    driver = FakeDriver([el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 0.0}}]}),
        clock=FakeClock(),
        transitions=lambda: [(ScreenTransition(kind="detail"), 0.0)],
        cancelled=_CancelAfter(1),
    )
    assert result.ok
    assert result.failure is None


def test_cancel_inside_the_alert_guard_retry_does_not_burn_its_timeout() -> None:
    """The alert guard's end-of-step retry re-enters `_wait`, so it needs the cancel source too.

    That retry is a second, hand-written `_run_step_body` call, and its `cancelled` parameter defaults
    to "never cancelled" — so omitting it fails nothing loudly, it just makes a `/cancel` invisible for
    the whole of a retried wait, doubling how long a cancelled run holds on.

    The cancel is armed by the guard firing rather than by a read count: the first wait has to time out
    *uncancelled* for the retry to exist at all, and counting reads to land after that would encode the
    poll cadence. So the observable is the clock — one timeout's worth of waiting, not two.
    """
    clock = FakeClock()
    fired = False

    def on_dismiss(d: AlertingDriver) -> None:
        # The end-of-step dismiss cleared the prompt. Clear the screen so the retry is a genuine
        # re-wait, and arm the cancel for that retry alone.
        nonlocal fired
        d.screen = []
        fired = True

    driver = AlertingDriver(
        [el("blocker", "Allow", ["button"])], label="Allow", on_dismiss=on_dismiss
    )
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 10.0}}]}),
        clock=clock,
        cancelled=lambda: fired,
        alert_guard=AlertGuardConfig(labels=["Allow"]),
    )
    assert not result.ok
    assert fired, "the alert guard never fired, so the retry path was never exercised"
    assert clock.now() < 15.0, (
        f"the retry burned its own full timeout too ({clock.now()}s of a 10s wait) — "
        "the cancel source never reached it"
    )
