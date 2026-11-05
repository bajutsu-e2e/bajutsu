"""A fake backend that answers alerts inside the requests that read the screen (BE-0461)."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Literal

from bajutsu.common.drivers import base
from bajutsu.common.drivers.elements import tree_buttons
from bajutsu.common.evidence.network import NetworkExchange

from .fake_driver import FakeDriver, React

_Rule = tuple[frozenset[str], str, frozenset[str]]


def _matches(rule: _Rule, labels: Sequence[str]) -> bool:
    """The runner's accept test: every identifying label exactly once, no excluded one at all."""
    identify, _, exclude = rule
    return all(labels.count(label) == 1 for label in identify) and not any(
        label in labels for label in exclude
    )


class ResolvingFakeDriver(FakeDriver):
    """`FakeDriver` plus `RESOLVE_ALERTS`, answering by the pushed policy the way the runner does.

    A deliberately small model of `AlertResolver` (BajutsuKit): a resolving read presses the
    SpringBoard alert a native rule identifies (clearing `system_alert_buttons`), takes the tree,
    and taps the in-tree prompt the first matching in-tree rule names through `tap`, so a `react`
    callback scripts the sheet leaving. It keeps no pacing memo: a test that needs `withheld`, a
    give-up, or any other reply queues it in `resolving_reads` / `alert_resolutions`, which win over
    the model while they last. Every record a reply carries also lands in this driver's drain, as
    the runner's whole store does.
    """

    CAPABILITIES = FakeDriver.CAPABILITIES | {base.Capability.RESOLVE_ALERTS}

    def __init__(
        self,
        screen: Sequence[base.Element] | None = None,
        react: React | None = None,
        exchanges: Sequence[NetworkExchange] | None = None,
        viewport: base.Point | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(screen, react, exchanges, viewport, now)
        self.native_rules: list[_Rule] = []
        self.in_tree_rules: list[_Rule] = []
        self.governs = False
        # Replies to hand back verbatim, oldest first, before the model answers again.
        self.resolving_reads: list[base.ResolvingRead] = []
        self.alert_resolutions: list[base.SystemAlertResolution] = []
        # When set, a native press lands but leaves the alert up (a press iOS did not act on).
        self.sticky_alert = False
        self._resolved = base.DrainedInterruptions.empty()

    def set_interruption_policy(
        self,
        rules: Sequence[tuple[frozenset[str], str, frozenset[str]]],
        governs: bool,
        *,
        in_tree_rules: Sequence[tuple[frozenset[str], str, frozenset[str]]] = (),
        pacing: base.AlertPacing | None = None,
        scenario_start: bool = False,
    ) -> None:
        """Record the push as `FakeDriver` does, and keep the rules this model matches."""
        super().set_interruption_policy(
            rules,
            governs,
            in_tree_rules=in_tree_rules,
            pacing=pacing,
            scenario_start=scenario_start,
        )
        self.native_rules = list(rules)
        self.in_tree_rules = list(in_tree_rules)
        self.governs = governs
        # `setPolicy` clears the runner's pending records on every push.
        self._resolved = base.DrainedInterruptions.empty()

    def query_resolving(
        self, mode: base.ResolveMode, *, force_check: bool = False
    ) -> base.ResolvingRead:
        self._record("query_resolving", (mode, force_check))
        if self.resolving_reads:
            read = self.resolving_reads.pop(0)
            self._resolved = self._resolved.merged_with(read.drained)
            return read
        drained = base.DrainedInterruptions.empty()
        checked = False
        if mode == "true":
            drained, checked = self._resolve_springboard()
        elements = self.query()
        in_tree = self._resolve_in_tree(elements)
        if in_tree is not None:
            drained = drained.merged_with(in_tree)
            elements = self.query()
        self._resolved = self._resolved.merged_with(drained)
        return base.ResolvingRead(elements=elements, drained=drained, springboard_checked=checked)

    def resolve_system_alert(self, labels: Sequence[str]) -> base.SystemAlertResolution:
        self._record("resolve_system_alert", list(labels))
        if self.alert_resolutions:
            resolution = self.alert_resolutions.pop(0)
            self._resolved = self._resolved.merged_with(resolution.drained)
            return resolution
        showing = self.system_alert_labels()
        if not showing:
            return base.SystemAlertResolution("absent", base.DrainedInterruptions.empty())
        if sorted(showing) != sorted(labels):
            return base.SystemAlertResolution("changed", base.DrainedInterruptions.empty())
        drained, _ = self._resolve_springboard()
        self._resolved = self._resolved.merged_with(drained)
        status: base.SystemAlertResolutionStatus = "answered" if drained.tapped else "unidentified"
        return base.SystemAlertResolution(status, drained)

    def drain_interruptions(self) -> base.DrainedInterruptions:
        """The seeded records `FakeDriver` hands back, after what the resolving requests drained."""
        resolved, self._resolved = self._resolved, base.DrainedInterruptions.empty()
        return resolved.merged_with(super().drain_interruptions())

    def _resolve_springboard(self) -> tuple[base.DrainedInterruptions, bool]:
        """Press the alert a native rule identifies; the records, and whether no alert was up."""
        labels = self.system_alert_labels()
        if not labels:
            return base.DrainedInterruptions.empty(), True
        rule = next((r for r in self.native_rules if _matches(r, labels)), None)
        if rule is None:
            unidentified = [labels] if self.governs else []
            return base.DrainedInterruptions(
                tapped=[], declined=[], banners=[], unidentified=unidentified
            ), False
        if not self.sticky_alert:
            self.system_alert_buttons = []
        # After the alert clears, so a `react` callback can script what the press revealed.
        self._record("resolve_press", rule[1])
        return self._answered(rule[1], labels, "native"), False

    def _resolve_in_tree(self, elements: list[base.Element]) -> base.DrainedInterruptions | None:
        """Tap the prompt the first matching in-tree rule names; its record, or None."""
        buttons = tree_buttons(elements)
        rule = next((r for r in self.in_tree_rules if _matches(r, buttons)), None)
        if rule is None or buttons.count(rule[1]) != 1:
            return None
        self.tap({"label": rule[1], "traits": [base.Trait.BUTTON]})
        return self._answered(rule[1], sorted(rule[0]), "inTree")

    @staticmethod
    def _answered(
        label: str, alert: list[str], surface: Literal["native", "inTree"]
    ) -> base.DrainedInterruptions:
        return base.DrainedInterruptions(
            tapped=[label],
            declined=[],
            banners=[],
            tapped_alerts=[alert],
            tapped_surfaces=[surface],
        )
