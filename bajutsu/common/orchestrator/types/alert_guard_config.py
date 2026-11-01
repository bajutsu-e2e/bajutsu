"""The reactive system-alert guard's per-scenario configuration (BE-0315)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from bajutsu.common.drivers import base

from .resolved_alert_rule import ResolvedAlertRule

# The guard's default SpringBoard-check cadence (seconds), overridable per scenario / target / flag
# via a `systemAlert` interrupts entry's `pollInterval` (BE-0315, BE-0473). A backend that
# answers alerts inside its own reads checks SpringBoard at most this often (BE-0461).
DEFAULT_ALERT_POLL_INTERVAL = 1.0

# How long a guarded wait tolerates a frozen screen before it stops, overridable the same way via
# a `systemAlert` interrupts entry's `frozenScreenTimeout`; 0 turns the stop off. Well past the sub-second collapse a
# sheet's own dismiss animation causes (measured: under a second on iOS 26.5), and short enough to
# leave a typical wait's budget unspent.
DEFAULT_FROZEN_SCREEN_TIMEOUT = 10.0

# The pacing the guard hands the XCUITest runner (BE-0461), which applies it to every showing it
# presses. Min seconds before a showing is pressed again: a press the runner accepts does not always
# land (measured on iOS — `TouchEventsCompleted` confirmed, app unmoved), and a prompt that stays up is
# indistinguishable, at the next read, from one merely fading out, so the delay waits past any real
# dismiss animation before concluding the press did not land.
_TREE_RETAP_DELAY = 1.0
# Presses spent on one showing that never clears: a prompt still up after this many is not one more
# press will fix, so the runner records *gave up* and the wait runs to its own timeout.
_TREE_DISMISS_MAX_TAPS = 3
# The floor under the give-up bound for an in-tree button that stays present but not hittable (a
# scrim mid-animation): a UIKit sheet presents in ~0.35-0.5s.
_TREE_DISMISS_DECLINE_GIVEUP_FLOOR = 2.0


def _decline_giveup(poll_interval: float) -> float:
    """Seconds the runner tolerates an unhittable in-tree button on one showing before it gives up.

    Twice the SpringBoard-check cadence, floored at the animation horizon: the in-tree tap waits on a
    clear check, so anything under two intervals would spend itself on the first attempt. Derived
    from the interval rather than fixed, so a scenario widening `pollInterval` (the save-password one
    sets 5) still gets a second attempt.
    """
    return max(_TREE_DISMISS_DECLINE_GIVEUP_FLOOR, 2 * poll_interval)


def _widest_first(rules: Iterable[ResolvedAlertRule]) -> list[ResolvedAlertRule]:
    """*rules*, with a nested shape's wider sibling moved ahead of it — the only reordering this makes
    deliberately.

    The runner matches in-tree rules in the order they arrive, and `savePassword`'s shapes nest: a
    sheet showing the widest shape's buttons also matches the narrower one, so the wider must be
    tried first. It is not otherwise order-preserving: the wider rule is inserted directly ahead of
    the first rule it strictly contains, so every rule declaration order put between the two moves
    back a place with it (`narrower`, `unrelated`, `widest` yields `widest`, `narrower`,
    `unrelated`). Subset is only a partial order, so no placement can leave every non-nesting pair
    where declaration put it. What BE-0177 needs is narrower than that and does hold: two layers
    declaring the same prompt resolve to the same shapes, so the scenario's copy of a shape stays
    ahead of the target's copy of it — which a plain width sort would not keep.

    A stable insertion pass rather than adjacent swaps: one non-nesting rule between a narrow shape
    and its wider sibling would block every adjacent swap.
    """
    result: list[ResolvedAlertRule] = []
    for rule in rules:
        for i, placed in enumerate(result):
            if placed.identifying_labels < rule.identifying_labels:
                result.insert(i, rule)
                break
        else:
            result.append(rule)
    return result


@dataclass
class AlertGuardConfig:
    """The reactive system-alert guard's per-scenario configuration (BE-0315).

    `rules` are the whole policy (BE-0406): each answers one named prompt regardless of which label
    it shares with another, and an alert no rule identifies is left alone rather than answered by a
    guessed button. The XCUITest backend applies them inside the requests that read the screen
    (BE-0461); every path here is deterministic, and no model is ever consulted (BE-0402).
    `poll_interval` is the SpringBoard-check cadence, decoupled from a wait's own condition poll.
    """

    rules: list[ResolvedAlertRule] = field(default_factory=list)
    poll_interval: float = DEFAULT_ALERT_POLL_INTERVAL
    frozen_screen_timeout: float = DEFAULT_FROZEN_SCREEN_TIMEOUT

    @property
    def tree_rules(self) -> list[ResolvedAlertRule]:
        """The rules for a prompt inside the application's own tree, in declaration order.

        Arming the in-tree pass on *any* rule would widen the tree match past what the author asked
        for — a scenario declaring `notifications` alone would arm one for a prompt that only ever
        appears in SpringBoard, and an application screen happening to show identifier-less "Allow"
        and "Don't Allow" buttons would be tapped (BE-0406).
        """
        return [rule for rule in self.rules if rule.in_tree]

    @property
    def tree_dedup_rules(self) -> list[ResolvedAlertRule]:
        """`tree_rules` with a nested shape's wider sibling moved ahead of it (`_widest_first`): the
        order the runner matches in-tree rules in, and the one the gate's give-up check reads."""
        return _widest_first(self.tree_rules)

    @property
    def pacing(self) -> base.AlertPacing:
        """The pacing a backend that answers alerts inside a request applies (BE-0461)."""
        return base.AlertPacing(
            poll_interval=self.poll_interval,
            retap_delay=_TREE_RETAP_DELAY,
            max_taps=_TREE_DISMISS_MAX_TAPS,
            give_up=_decline_giveup(self.poll_interval),
        )

    @property
    def native_rules(self) -> list[ResolvedAlertRule]:
        """The rules for a prompt SpringBoard raises, in declaration order.

        Filtered on `native`, never the full `rules`: an in-tree-only shape's identifying labels are
        ordinary vocabulary a real SpringBoard alert could coincidentally offer (the 26.5 save
        sheet's shape is just "Save" / "Not Now"), and matching it there would answer a prompt that
        surface can never reach.
        """
        return [rule for rule in self.rules if rule.native]
