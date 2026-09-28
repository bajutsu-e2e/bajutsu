"""One `interrupts` handler: a screen that can appear at an unpredictable point (BE-0314)."""

from __future__ import annotations

from pydantic import Field

from bajutsu.common.scenario.models._base import _Model
from bajutsu.common.scenario.models.assertions import Assertion

from .step import Step


class Interrupt(_Model):
    """A handler for an interstitial screen that can appear at an unpredictable point (BE-0314).

    ``condition`` is the same assertion the ``if`` step evaluates; the runner checks it
    opportunistically against trees it has already fetched (a ``wait``'s poll tick, an act step's
    pre-action read), wherever in the step sequence the screen happens to surface, and runs
    ``steps`` to clear it when it matches. The steps share the enclosing scenario's ``vars.*``, the
    same as ``if``'s branches do.

    ``target`` names which of the scenario's declared targets the entry watches (BE-0438). Unlike
    ``Step.target`` it stays optional however many targets the scenario declares: an omitted value
    resolves to the primary target. A step in ``steps`` that omits its own ``target`` runs on the
    target the entry watches, not on the primary one — or, nested under an ``if`` / ``forEach``
    that names a target, on that step's target.
    """

    condition: Assertion
    steps: list[Step] = Field(default_factory=list)
    target: str | None = None
