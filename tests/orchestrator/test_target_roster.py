"""Tests for the scenario-wide target roster: member lifecycle and the moving primary (BE-0447).

The roster decides two things the step loop reads on every step: whether a device-group member can
take a step of a given kind, and which `interrupts` entries each runner polls. Both are plain data
decisions, checked here without a driver; the routing tests below drive them through
`run_scenario`, where a step addressed to an unavailable member must fail as an ordinary step and
an omitted-target interrupt must follow the primary the roster holds, not the one the run started
with.
"""

from __future__ import annotations

from typing import cast

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.orchestrator import (
    DeviceControl,
    MemberStatus,
    TargetRoster,
    TargetRuntime,
    run_scenario,
)
from bajutsu.common.scenario import Interrupt


def _interrupt(target: str | None = None, ident: str = "popup") -> Interrupt:
    data: dict[str, object] = {
        "condition": {"exists": {"id": ident}},
        "steps": [{"tap": {"id": "dismiss"}}],
    }
    if target is not None:
        data["target"] = target
    return Interrupt.model_validate(data)


# --- member lifecycle ----------------------------------------------------------------------------


def test_an_untracked_target_is_running() -> None:
    roster = TargetRoster(primary="app")
    assert roster.status_of("app") is MemberStatus.RUNNING
    assert roster.unavailable("app", "tap") is None


def test_a_member_not_installed_refuses_every_step_it_knows() -> None:
    roster = TargetRoster(primary="old", status={"new": MemberStatus.NOT_INSTALLED})
    for action in ("tap", "foreground"):
        reason = roster.unavailable("new", action)
        assert reason is not None and "not installed yet" in reason


def test_an_installed_member_takes_only_foreground() -> None:
    roster = TargetRoster(primary="old", status={"new": MemberStatus.INSTALLED})
    assert roster.unavailable("new", "foreground") is None
    reason = roster.unavailable("new", "tap")
    assert reason is not None and "has not launched yet" in reason and "foreground" in reason


def test_a_retired_member_refuses_every_step() -> None:
    roster = TargetRoster(primary="new", status={"old": MemberStatus.RETIRED})
    reason = roster.unavailable("old", "foreground")
    assert reason is not None and "retired" in reason


def test_mark_moves_a_member_through_its_lifecycle() -> None:
    roster = TargetRoster(primary="old", status={"new": MemberStatus.NOT_INSTALLED})
    roster.mark("new", MemberStatus.INSTALLED)
    assert roster.status_of("new") is MemberStatus.INSTALLED
    roster.mark("new", MemberStatus.RUNNING)
    assert roster.unavailable("new", "tap") is None


# --- interrupts follow the current primary -------------------------------------------------------


def test_an_omitted_target_entry_follows_the_current_primary() -> None:
    floating, pinned = _interrupt(), _interrupt("web", "banner")
    roster = TargetRoster(primary="app", entries=[floating, pinned])
    assert roster.interrupts_for("app", []) == [floating]
    assert roster.interrupts_for("web", []) == [pinned]
    roster.set_primary("web")
    assert roster.interrupts_for("app", []) == []
    assert roster.interrupts_for("web", []) == [floating, pinned]


def test_config_entries_come_first_and_scenario_entries_keep_their_order() -> None:
    config = _interrupt("app", "config")
    first, second = _interrupt("app", "first"), _interrupt(None, "second")
    roster = TargetRoster(primary="app", entries=[first, second])
    assert roster.interrupts_for("app", [config]) == [config, first, second]


def test_a_member_that_is_not_running_polls_nothing() -> None:
    config = _interrupt("new", "config")
    roster = TargetRoster(
        primary="new", entries=[_interrupt()], status={"new": MemberStatus.INSTALLED}
    )
    assert not roster.polls("new")
    assert roster.interrupts_for("new", [config]) == []
    roster.mark("new", MemberStatus.RUNNING)
    assert len(roster.interrupts_for("new", [config])) == 2


# --- through the step loop -----------------------------------------------------------------------

_OLD = [el("old.button")]
_NEW = [el("new.button")]


def test_a_step_addressed_to_an_uninstalled_member_fails_by_name() -> None:
    old = FakeDriver(screen=list(_OLD))
    r = run_scenario(
        old,
        _scenario(
            {
                "name": "group",
                "targets": [["old", "new"]],
                "primaryTarget": "old",
                "steps": [{"tap": {"id": "old.button"}}, {"target": "new", "tap": {"id": "x"}}],
            }
        ),
        FakeClock(),
        target_runtimes={"old": TargetRuntime(driver=old, sink=NullSink())},
        primary_target="old",
        roster=TargetRoster(primary="old", status={"new": MemberStatus.NOT_INSTALLED}),
    )
    assert not r.ok
    assert "target 'new' is not installed yet" in (r.failure or "")
    assert [o.ok for o in r.steps] == [True, False]
    assert r.steps[1].target == "new"


class _Foreground:
    """A `DeviceControl` double that counts `foreground()` calls."""

    def __init__(self) -> None:
        self.calls = 0

    def foreground(self) -> None:
        self.calls += 1


def test_an_installed_member_comes_up_at_its_first_foreground() -> None:
    old, new = FakeDriver(screen=list(_OLD)), FakeDriver(screen=list(_NEW))
    control = _Foreground()
    activated: list[str] = []

    def activate(name: str) -> TargetRuntime:
        activated.append(name)
        return TargetRuntime(driver=new, sink=NullSink(), control=cast(DeviceControl, control))

    roster = TargetRoster(primary="old", status={"new": MemberStatus.INSTALLED}, activate=activate)
    r = run_scenario(
        old,
        _scenario(
            {
                "name": "group",
                "targets": [["old", "new"]],
                "primaryTarget": "old",
                "steps": [
                    {"target": "new", "foreground": {}},
                    {"target": "new", "tap": {"id": "new.button"}},
                ],
            }
        ),
        FakeClock(),
        target_runtimes={"old": TargetRuntime(driver=old, sink=NullSink())},
        primary_target="old",
        roster=roster,
    )
    assert r.ok, r.failure
    assert activated == ["new"]
    assert control.calls == 1
    assert ("tap", {"id": "new.button"}) in new.actions
    assert roster.status_of("new") is MemberStatus.RUNNING


def test_an_omitted_target_interrupt_polls_on_the_rosters_primary() -> None:
    # The run started on `app`, but the roster's primary is `web`: the entry omitting `target`
    # must fire against `web`'s screen, not `app`'s, even though both show the popup.
    popup = [el("popup"), el("dismiss"), el("go")]
    app, web = FakeDriver(screen=list(popup)), FakeDriver(screen=list(popup))
    r = run_scenario(
        app,
        _scenario(
            {
                "name": "moved",
                "targets": ["app", "web"],
                "primaryTarget": "app",
                "steps": [{"target": "web", "tap": {"id": "go"}}],
            }
        ),
        FakeClock(),
        target_runtimes={
            "app": TargetRuntime(driver=app, sink=NullSink()),
            "web": TargetRuntime(driver=web, sink=NullSink()),
        },
        primary_target="app",
        roster=TargetRoster(primary="web", entries=[_interrupt()]),
    )
    assert r.ok, r.failure
    assert ("tap", {"id": "dismiss"}) in web.actions
    assert ("tap", {"id": "dismiss"}) not in app.actions


def test_an_installed_member_with_no_activation_wired_fails_loudly() -> None:
    old = FakeDriver(screen=list(_OLD))
    with pytest.raises(RuntimeError, match="no activation was wired"):
        run_scenario(
            old,
            _scenario(
                {
                    "name": "group",
                    "targets": [["old", "new"]],
                    "primaryTarget": "old",
                    "steps": [{"target": "new", "foreground": {}}],
                }
            ),
            FakeClock(),
            target_runtimes={"old": TargetRuntime(driver=old, sink=NullSink())},
            primary_target="old",
            roster=TargetRoster(primary="old", status={"new": MemberStatus.INSTALLED}),
        )


def test_a_member_brought_up_in_steps_stays_routable_from_after() -> None:
    old, new = FakeDriver(screen=list(_OLD)), FakeDriver(screen=list(_NEW))
    control = _Foreground()

    def activate(name: str) -> TargetRuntime:
        return TargetRuntime(driver=new, sink=NullSink(), control=cast(DeviceControl, control))

    r = run_scenario(
        old,
        _scenario(
            {
                "name": "group",
                "targets": [["old", "new"]],
                "primaryTarget": "old",
                "steps": [{"target": "new", "foreground": {}}],
                "after": [
                    {"on": "always", "steps": [{"target": "new", "tap": {"id": "new.button"}}]}
                ],
                "expect": [{"target": "new", "exists": {"id": "new.button"}}],
            }
        ),
        FakeClock(),
        target_runtimes={"old": TargetRuntime(driver=old, sink=NullSink())},
        primary_target="old",
        roster=TargetRoster(
            primary="old", status={"new": MemberStatus.INSTALLED}, activate=activate
        ),
    )
    assert r.ok, r.failure
    assert ("tap", {"id": "new.button"}) in new.actions


def test_a_member_that_cannot_start_fails_its_foreground_step() -> None:
    old = FakeDriver(screen=list(_OLD))

    def activate(name: str) -> TargetRuntime:
        raise TimeoutError("the app never reached the foreground")

    r = run_scenario(
        old,
        _scenario(
            {
                "name": "group",
                "targets": [["old", "new"]],
                "primaryTarget": "old",
                "steps": [{"target": "new", "foreground": {}}],
            }
        ),
        FakeClock(),
        target_runtimes={"old": TargetRuntime(driver=old, sink=NullSink())},
        primary_target="old",
        roster=TargetRoster(
            primary="old", status={"new": MemberStatus.INSTALLED}, activate=activate
        ),
    )
    assert not r.ok
    assert "target 'new' could not start: the app never reached the foreground" in (r.failure or "")
