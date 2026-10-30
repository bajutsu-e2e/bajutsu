"""The interruption policy pushed to the backend and the drain that reads it back (BE-0399, BE-0406).

Kept from the guard's earlier test suite when BE-0461 moved alert resolution into the runner: the
policy push and the drain are what the runner's resolution still rests on.
"""

from __future__ import annotations

from typing import cast

import pytest
from conftest import el, guard_rule

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertEvent, AlertGuardConfig
from bajutsu.common.orchestrator.types import ResolvedAlertRule


def _button(label: str) -> base.Element:
    return el(label=label, traits=[base.Trait.BUTTON])


def test_push_interruption_policy_hands_the_backend_the_guard_s_own_rules() -> None:
    # The decision stays here: what the backend receives is the scenario's own resolved rules, plus
    # `governs=True`, so the runner reports (rather than guesses at) any alert none of them identify.
    # Without this the backend answers an alert that interrupts one of its own interactions with the
    # alert's *default* button, which is the opposite of the least-destructive policy and reaches no
    # report.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    rule = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don't Allow"}), tap_label="Don't Allow"
    )
    push_interruption_policy(driver, AlertGuardConfig(rules=[rule]))
    assert driver.interruption_policy == ([({"Allow", "Don't Allow"}, "Don't Allow")], True)


def test_push_interruption_policy_still_governs_without_rules() -> None:
    # A scenario that declares no rules of its own still governs — a guard being on at all is what
    # decides whether a declined alert gets reported, independent of whether any rule survived the
    # in-tree-only drop below (BE-0406 Unit 2b): a real declaration filtered down to nothing this
    # surface can act on is not the same as no declaration at all.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, AlertGuardConfig())
    assert driver.interruption_policy == ([], True)


def test_push_interruption_policy_omits_a_rule_the_backend_can_never_meet() -> None:
    # This surface exists for an alert in *another* process interrupting an XCUITest interaction, so
    # a prompt raised into the application's own process never reaches it. Pushing such a rule anyway
    # would not merely be untidy: the Swift side matches by subset, so an in-tree-only shape would
    # re-open on the runner the collision `excluded_labels` closes here (BE-0406).
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    reachable = guard_rule("Don't Allow", identifying=("Allow", "Don't Allow"), in_tree=False)
    in_tree_only = guard_rule("Not Now", identifying=("Save", "Not Now"), native=False)
    push_interruption_policy(driver, AlertGuardConfig(rules=[reachable, in_tree_only]))
    assert driver.interruption_policy is not None
    assert driver.interruption_policy[0] == [({"Allow", "Don't Allow"}, "Don't Allow")]
    # It travels on the in-tree list instead, which the runner's monitor never matches (BE-0461).
    assert driver.interruption_in_tree_rules == [({"Save", "Not Now"}, "Not Now", set())]


def test_push_interruption_policy_sends_in_tree_rules_widest_first_with_the_guard_s_pacing() -> (
    None
):
    # The runner answers an application-owned prompt inside a resolving read (BE-0461 Unit 2), so it
    # needs the in-tree rules in the one order every in-tree match reads (`tree_dedup_rules`), and
    # the pacing the gate applies to its own taps, so a showing is re-pressed and given up on alike
    # whichever side answers it.
    from bajutsu.common.orchestrator import push_interruption_policy
    from bajutsu.common.orchestrator.types.alert_guard_config import (
        _TREE_DISMISS_MAX_TAPS,
        _TREE_RETAP_DELAY,
        _decline_giveup,
    )

    driver = FakeDriver([])
    narrow = guard_rule("Not Now", identifying=("Save", "Not Now"), native=False)
    wide = guard_rule("Not Now", identifying=("Save", "Not Now", "Never"), native=False)
    guard = AlertGuardConfig(rules=[narrow, wide], poll_interval=5.0)
    push_interruption_policy(driver, guard)
    assert driver.interruption_in_tree_rules == [
        ({"Save", "Not Now", "Never"}, "Not Now", set()),
        ({"Save", "Not Now"}, "Not Now", set()),
    ]
    assert driver.interruption_pacing == base.AlertPacing(
        poll_interval=5.0,
        retap_delay=_TREE_RETAP_DELAY,
        max_taps=_TREE_DISMISS_MAX_TAPS,
        give_up=_decline_giveup(5.0),
    )
    assert driver.interruption_scenario_start is False


def test_push_interruption_policy_marks_the_scenario_start_push() -> None:
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, AlertGuardConfig(), scenario_start=True)
    assert driver.interruption_scenario_start is True


def test_push_interruption_policy_refuses_a_rule_claiming_both_surfaces() -> None:
    # No declared prompt shows on both surfaces; pushing such a rule on one would silently drop the
    # other, so it fails loudly instead (BE-0461 Unit 2).
    from bajutsu.common.orchestrator import push_interruption_policy

    with pytest.raises(ValueError, match="both the native and the in-tree surface"):
        push_interruption_policy(FakeDriver([]), AlertGuardConfig(rules=[guard_rule("OK")]))


def test_push_interruption_policy_without_a_guard_sends_no_pacing() -> None:
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, None)
    assert driver.interruption_pacing is None
    assert driver.interruption_in_tree_rules == []


def test_push_interruption_policy_carries_a_reachable_rule_s_exclusion_set() -> None:
    # `notifications` excludes the Local Network title's marker; dropping the exclusion on the way to
    # the runner would let the monitor answer that prompt by the notification rule.
    from bajutsu.common.orchestrator import push_interruption_policy

    excluding = ResolvedAlertRule(
        identifying_labels=frozenset({"Allow", "Don’t Allow"}),
        tap_label="Allow",
        excluded_labels=frozenset({"title: Allow “%@” to find devices on local networks?"}),
        native=True,
    )
    driver = FakeDriver([])
    push_interruption_policy(driver, AlertGuardConfig(rules=[excluding]))
    assert driver.interruption_policy == (
        [
            (
                {"Allow", "Don’t Allow"},
                "Allow",
                {"title: Allow “%@” to find devices on local networks?"},
            )
        ],
        True,
    )


def test_push_interruption_policy_clears_it_when_the_scenario_disables_the_guard() -> None:
    # A run under `--no-system-alert-handling` must not inherit the previous scenario's policy from the resident
    # runner, so the push happens with an empty, non-governing policy rather than being skipped. Not
    # governing is what keeps this the one case that still leaves a declined alert unreported —
    # declaring nothing is the author's own choice, not an omission this mechanism exists to catch.
    from bajutsu.common.orchestrator import push_interruption_policy

    driver = FakeDriver([])
    push_interruption_policy(driver, None)
    assert driver.interruption_policy == ([], False)


def test_drain_interruptions_reports_what_the_backend_answered_as_alert_events() -> None:
    # A prompt answered inside the backend's interruption handling is still a prompt this run
    # dismissed; reporting it is what keeps that dismissal out of the silence the mechanism exists
    # to end.
    from bajutsu.common.orchestrator import drain_interruptions

    driver = FakeDriver([])
    driver.interruptions_to_drain = ["Don't Allow", "Not Now"]
    drained = drain_interruptions(driver)
    assert drained.alerts == [AlertEvent(label="Don't Allow"), AlertEvent(label="Not Now")]
    assert drained.undeclared == []
    again = drain_interruptions(driver)
    assert again.alerts == []  # drained, not repeated onto the next step


def test_drain_interruptions_reports_what_the_backend_declined_as_undeclared_interruptions() -> (
    None
):
    # A declined alert is not a dismissal — nothing answered it on the scenario's behalf — so it is
    # reported through the separate `undeclared` list, buttons intact, for the caller to fail by name
    # rather than let the interruption pass in silence (BE-0406 Unit 2b).
    from bajutsu.common.orchestrator import drain_interruptions
    from bajutsu.common.orchestrator.types import UndeclaredInterruption

    driver = FakeDriver([])
    driver.interruptions_declined_to_drain = [["Save", "Not Now"], ["Allow", "Don't Allow"]]
    drained = drain_interruptions(driver)
    assert drained.alerts == []
    assert drained.undeclared == [
        UndeclaredInterruption(buttons=["Save", "Not Now"]),
        UndeclaredInterruption(buttons=["Allow", "Don't Allow"]),
    ]
    again = drain_interruptions(driver)
    assert again.undeclared == []  # drained, not repeated


def test_drain_interruptions_reports_a_swiped_banner_as_its_own_kind_of_alert_event() -> None:
    # A banner cleared at interruption time is a dismissal, so it belongs in `alerts` — but under a
    # kind of its own, since its `label` is the notification's text rather than a button anything
    # pressed (BE-0416). Without the discriminator the report shows it as an alert dismissal naming
    # a button that never existed.
    from bajutsu.common.orchestrator import drain_interruptions

    driver = FakeDriver([])
    driver.interruptions_to_drain = ["Not Now"]
    driver.banners_to_drain = ["Ready for Apple Intelligence"]
    drained = drain_interruptions(driver)
    assert drained.alerts == [
        AlertEvent(label="Not Now"),
        AlertEvent(label="Ready for Apple Intelligence", kind="notificationBanner"),
    ]
    again = drain_interruptions(driver)
    assert again.alerts == []  # drained, not repeated onto the next step


def test_a_swiped_banner_is_never_an_undeclared_interruption() -> None:
    # The defect BE-0416 Unit 1 measured: a banner reaching the alert path could match no rule, so a
    # governed run recorded it as undeclared and failed an otherwise-passing step, naming the
    # notification's body text as a button. No scenario can declare a banner — it offers no button to
    # identify — so failing over one would fail every run a notification happened to land in.
    from bajutsu.common.orchestrator import drain_interruptions

    driver = FakeDriver([])
    driver.banners_to_drain = ["Ready for Apple Intelligence"]
    assert drain_interruptions(driver).undeclared == []


def test_the_interruption_policy_is_skipped_on_a_backend_without_the_opt_in() -> None:
    # A narrow opt-in: only XCUITest interposes on an interaction this way, so a backend that does
    # not implement it is never asked and contributes no events — the run is otherwise unchanged.
    from bajutsu.common.orchestrator import drain_interruptions, push_interruption_policy

    class _NoOptIn:
        """A backend stub carrying neither half of the opt-in (a web / Android shape)."""

    driver = cast("base.Driver", _NoOptIn())
    push_interruption_policy(driver, AlertGuardConfig())
    drained = drain_interruptions(driver)
    assert drained.alerts == []
    assert drained.undeclared == []
