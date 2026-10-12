"""Route a step's guarded tree reads through the backend's alert resolution (BE-0461 Unit 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast, get_protocol_members

from bajutsu.common.drivers import base
from bajutsu.common.drivers.tracing import _PROTOCOLS
from bajutsu.common.orchestrator.types import alert_block_note, uncleared_prompt_note


@dataclass
class ResolvingReads:
    """One step's resolving reads: the requests that answer an alert where the step reads the screen.

    Built only for a step under a governing guard on a backend advertising `RESOLVE_ALERTS`. The
    wait loop and the action handlers' selector-resolution reads go through `view`, so their
    `query()` sends a resolving `/elements`; everything else — the assertions, evidence, a TipKit
    re-read — keeps the plain `Driver.query` and so keeps seeing the real screen.

    It holds no state a re-run could re-arm: each read is a fact of its own request, and the records
    it returns are only read here — the drain that consumes them reports them.
    """

    target: base.AlertResolvingTarget
    # The reads since the gate last took them, oldest first.
    _unseen: list[base.ResolvingRead] = field(default_factory=list)
    # Whether a read since `start_body` returned an *answered* record: the narrowed retry's licence
    # for an alert that read cleared and that is gone by the recovery read (Unit 5).
    answered: bool = False
    # Whether the body run since `start_body` failed on a refused actuation or a target its
    # selector-resolution read could not find — the failures an alert covering the screen explains,
    # and so the ones the narrowed retry may follow (Unit 5).
    refused: bool = False

    def read(
        self, mode: base.ResolveMode, *, force_check: bool = False, keep: bool = True
    ) -> base.ResolvingRead:
        """One resolving read, kept for the gate and the retry to look at unless *keep* is off."""
        result = self.target.query_resolving(mode, force_check=force_check)
        if keep:
            self._unseen.append(result)
        if result.drained.tapped:
            self.answered = True
        return result

    def take(self) -> list[base.ResolvingRead]:
        """The reads since the last call, oldest first, which this call hands over for good."""
        unseen, self._unseen = self._unseen, []
        return unseen

    def start_body(self) -> None:
        """Forget what an earlier body run read, so the retry judges only the run that failed."""
        self._unseen.clear()
        self.answered = False
        self.refused = False

    def note(self, recovery: base.ResolvingRead) -> str:
        """The blocked-screen note for a failed body run: the newest one its reads and *recovery* name.

        A showing records *gave up* once, so a give-up a body read returned is not repeated by the
        recovery read that follows; reading the body's unconsumed reads keeps it on the failure.
        """
        for read in reversed([*self.take(), recovery]):
            note = records_note(read.drained)
            if note:
                return note
        return ""

    def view(
        self, driver: base.Driver, mode: base.ResolveMode, *, keep: bool = True
    ) -> base.Driver:
        """*driver*, with `query()` sending a resolving read in *mode* and every other call unchanged.

        A forwarding view rather than a flag threaded through every handler: the reads a handler makes
        to resolve its target are ordinary `query()` calls, and a handle-based actuation's own read
        stays inside the driver, plain, as the design wants.
        """
        return cast("base.Driver", _ResolvingView(driver, self, mode, keep))


def records_note(drained: base.DrainedInterruptions) -> str:
    """The blocked-screen note one reply's records name, or empty when they name none.

    A give-up outranks an unidentified alert: it names the button a rule chose and could not clear
    the prompt with, which is the more specific fact (BE-0461 Unit 4).
    """
    if drained.gave_up:
        return uncleared_prompt_note(drained.gave_up[-1].tap)
    if drained.unidentified:
        return alert_block_note(drained.unidentified[-1])
    return ""


class _ResolvingView:
    """The forwarding view `ResolvingReads.view` returns."""

    def __init__(
        self, driver: base.Driver, reads: ResolvingReads, mode: base.ResolveMode, keep: bool
    ) -> None:
        self._driver = driver
        self._reads = reads
        self._mode: base.ResolveMode = mode
        self._keep = keep
        # Real attributes, not `__getattr__` alone: `isinstance` against a runtime protocol looks
        # members up statically, so a view forwarding by `__getattr__` would pass no protocol check
        # and hide the backend's own (the reason `TracingDriver` installs them the same way).
        for protocol in _PROTOCOLS:
            if isinstance(driver, protocol):
                for name in get_protocol_members(protocol):
                    setattr(self, name, getattr(driver, name))
        # Both reads a handler takes its target from resolve; an actuation-grade read included, which
        # would otherwise skip the alert.
        self.query = self._resolving_query  # type: ignore[method-assign]
        if isinstance(driver, base.SettledReadProvider):
            self.settled_query = self._resolving_query

    def _resolving_query(self) -> list[base.Element]:
        return self._reads.read(self._mode, keep=self._keep).elements

    def query(self) -> list[base.Element]:
        return self._resolving_query()

    def __getattr__(self, name: str) -> Any:
        # A member no protocol declares is still the driver's own.
        return getattr(self._driver, name)
