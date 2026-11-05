"""The seam for a backend that answers an alert interrupting one of its own interactions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .alert_pacing import AlertPacing
from .drained_interruptions import DrainedInterruptions


@runtime_checkable
class InterruptionPolicyTarget(Protocol):
    """A backend that answers an alert interrupting one of its *own* interactions, by our policy.

    A narrow opt-in, like `ViewportProvider` / `ActuationReporter`: a backend that does not implement
    it is simply never asked, and the run is otherwise unchanged. Only XCUITest needs it. XCUITest
    resolves an out-of-process alert that interrupts an interaction *before* it synthesizes that
    interaction, and with nothing installed it answers using the alert's own default button — the
    opposite of the least-destructive policy the guard applies, and invisible to the run's report.

    `set_interruption_policy` hands over the labels `AlertGuardConfig` has already resolved (a rule's
    identifying label set, the label it taps, and the labels that rule it out) and whether the guard governs this scenario at all,
    so the decision stays in the orchestrator and the backend only applies it. `drain_interruptions`
    takes back what it answered, what it declined, and what it swiped away, so a dismissal reaches
    the report as an `AlertEvent`, an undeclared interruption can fail the step/expect that met it
    (BE-0406), and a foreground notification banner is told apart from a dismissed alert (BE-0416),
    rather than any of the three happening silently.
    """

    def set_interruption_policy(
        self,
        rules: Sequence[tuple[frozenset[str], str, frozenset[str]]],
        governs: bool,
        *,
        in_tree_rules: Sequence[tuple[frozenset[str], str, frozenset[str]]] = (),
        pacing: AlertPacing | None = None,
        scenario_start: bool = False,
    ) -> None:
        """Hand the backend the buttons it may press on an interrupting alert.

        Each rule is its identifying labels, the label it taps, and the labels whose presence rules
        it out — the accept test the runner applies (`InterruptionRule.matches`, BajutsuKit): every
        identifying label on the alert exactly once, and no excluded label present.

        Args:
            rules: The rules for a prompt in another process (a SpringBoard alert), in match order.
            governs: Whether the scenario's guard is on at all.
            in_tree_rules: The rules for a prompt inside the application's own tree, in match order
                (`AlertGuardConfig.tree_dedup_rules`), for a backend that answers those itself
                inside a request (BE-0461). A backend that does not ignores them.
            pacing: How such a backend re-presses and gives up on one showing; `None` leaves the
                backend's own defaults.
            scenario_start: True on the push at scenario start only, which also forgets every
                showing a previous scenario answered.
        """
        ...

    def drain_interruptions(self) -> DrainedInterruptions:
        """What the backend answered, declined, and swiped away since the last call, oldest first in each."""
        ...
