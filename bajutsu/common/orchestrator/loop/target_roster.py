"""Where each declared target of a multi-target run stands, and which one is the primary now (BE-0447)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from bajutsu.common.orchestrator.types import TargetRuntime
from bajutsu.common.scenario import Interrupt


class MemberStatus(Enum):
    """A declared target's lifecycle on the device it shares with its device group (BE-0447).

    Every target that starts with the scenario is `RUNNING` from its first step. Only a later member
    of a device group — one neither the primary nor listed in `installs` — walks the other states:
    it is `NOT_INSTALLED` until its `installApp`, `INSTALLED` until its first `foreground`, and
    `RETIRED` once another member's build with the same identifier replaces its app.
    """

    NOT_INSTALLED = "not installed"
    INSTALLED = "installed, not launched"
    RUNNING = "running"
    RETIRED = "retired"


@dataclass
class TargetRoster:
    """The scenario-wide record of which targets can take a step, and which one is the primary.

    Scenario-scoped like `AppCrashLatches`: `run_scenario` hands every phase the same object, so a
    member `foreground` started in `steps` is still running for an `after` rule. The pipeline builds
    it, since only the pipeline holds the leases a later member joins.

    `primary` starts as the first member of the first group and moves when a `setPrimaryTarget` step
    runs. Steps never read it — their omitted `target` already resolved statically at load time — but
    an `interrupts` entry that omits `target` does: `entries` holds the scenario's own entries, each
    resolved against the current primary whenever a runner asks, so moving the primary moves an
    omitted-target entry's guard with it.

    `activate` brings a later member up on its group's device at its first `foreground`, returning
    the runtime its runner is built over. `None` where no later member exists. `runtimes` keeps every
    runtime `activate` returned, since each phase builds its runners afresh from the runtimes the
    run started with: a member brought up in `steps` must still be routable from `after` and
    `expect`.
    """

    primary: str
    entries: list[Interrupt] = field(default_factory=list)
    status: dict[str, MemberStatus] = field(default_factory=dict)
    activate: Callable[[str], TargetRuntime] | None = None
    runtimes: dict[str, TargetRuntime] = field(default_factory=dict)
    # Installs a later member's build for an `installApp` step: `(device target, member,
    # keep_data)`, returning the group's other members whose app shares the member's identifier —
    # which only the pipeline, holding each target's config, can tell. The roster retires those.
    install: Callable[[str, str, bool], list[str]] | None = None

    def live(self, started: Mapping[str, TargetRuntime] | None) -> dict[str, TargetRuntime]:
        """*started* plus every member brought up since, the map a phase routes through."""
        return {**(started or {}), **self.runtimes}

    def status_of(self, name: str) -> MemberStatus:
        """*name*'s status, `RUNNING` for every target the roster does not track."""
        return self.status.get(name, MemberStatus.RUNNING)

    def set_primary(self, name: str) -> None:
        """Make *name* the target an omitted-`target` `interrupts` entry follows from now on."""
        self.primary = name

    def mark(self, name: str, status: MemberStatus) -> None:
        """Record *name*'s new lifecycle status."""
        self.status[name] = status

    def unavailable(self, name: str, action: str) -> str | None:
        """Why a step of kind *action* cannot run against *name* now, or None when it can.

        A later member answers to its own lifecycle steps alone until it runs, so a step that
        would otherwise drive a stale build a reused device still holds, or an app that never came
        to the front, fails with a cause naming the step it is missing instead.
        """
        status = self.status_of(name)
        if status is MemberStatus.RUNNING or action == "install_app":
            # An `installApp` step's own target only picks the device, so neither the retired nor
            # the not-installed rule applies to it.
            return None
        if status is MemberStatus.RETIRED:
            return (
                f"target {name!r} is retired: another member's build replaced its app on the "
                "shared device, so address the member that installed it"
            )
        if status is MemberStatus.NOT_INSTALLED:
            return (
                f"target {name!r} is not installed yet: an installApp step must install it before "
                "any other step addresses it"
            )
        if action == "foreground":
            return None
        return (
            f"target {name!r} has not launched yet: bring it up with a foreground step addressed "
            "to it first"
        )

    def install_member(self, device: str, member: str, *, keep_data: bool) -> str | None:
        """Install *member*'s build on *device*'s group device, or why it cannot (BE-0447).

        A member installs once per scenario: an `installApp` inside a loop or a recovery that runs
        a second time fails rather than reinstall a member the scenario may already be driving.
        """
        if self.status_of(member) is not MemberStatus.NOT_INSTALLED:
            return (
                f"installApp from {member!r}: it was already installed in this scenario "
                f"(now {self.status_of(member).value}) — a later member installs once"
            )
        if self.install is None:
            raise RuntimeError(f"installApp from {member!r}: no install was wired")
        for other in self.install(device, member, keep_data):
            # A member whose build was never installed has no app to replace.
            if self.status_of(other) is not MemberStatus.NOT_INSTALLED:
                self.mark(other, MemberStatus.RETIRED)
        self.mark(member, MemberStatus.INSTALLED)
        return None

    def move_primary(self, name: str) -> str | None:
        """Make *name* the primary, or why it cannot be: a retired member answers to nothing."""
        if self.status_of(name) is MemberStatus.RETIRED:
            return (
                f"setPrimaryTarget {name!r}: that member is retired — another member's build "
                "replaced its app, so name the member that installed it"
            )
        self.set_primary(name)
        return None

    def polls(self, name: str) -> bool:
        """Whether *name*'s runner may poll its `interrupts` entries now (BE-0447).

        A member that is not running has no screen of its own to watch, and polling another
        member's tree in its place would fire its handlers against the wrong app.
        """
        return self.status_of(name) is MemberStatus.RUNNING

    def interrupts_for(self, name: str, config: list[Interrupt]) -> list[Interrupt]:
        """The `interrupts` entries *name*'s runner polls now (BE-0447).

        *config* is the target config's own entries for *name*, which always come first. The
        scenario's entries follow in declared order: those naming *name*, and those omitting
        `target` while *name* is the current primary.
        """
        if not self.polls(name):
            return []
        return [*config, *(e for e in self.entries if (e.target or self.primary) == name)]
