"""Secret values one scenario derived mid-run, and the variables that hold them (BE-0472)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from bajutsu.common.assertions import AssertionResult
from bajutsu.common.evidence.redaction import Redactor

from .alert_event import AlertEvent
from .run_result import RunResult


@dataclass
class DerivedSecrets:
    """What an `eval` step derived from a secret during one scenario.

    Scenario-scoped, never run-wide: a run shares its redactors across worker threads, and one
    scenario's derived value must not mask matching text in another scenario's evidence. One worker
    thread owns a scenario, so nothing here needs a lock.
    """

    # The raw values, in the order they were derived. The runner's own writers learn them as
    # listeners; `mask_derived_text` reads them to mask the finished result.
    values: list[str] = field(default_factory=list)
    # The `vars.*` names whose current value is secret, so an `eval` reading one of them derives a
    # secret in turn.
    secret_vars: set[str] = field(default_factory=set)
    # Called with each batch of new values, so a sink already writing this scenario's evidence masks
    # them from the next artifact on.
    listeners: list[Callable[[Sequence[str]], None]] = field(default_factory=list)

    def register(self, values: Sequence[str]) -> None:
        """Add *values* to the set every later artifact of this scenario masks."""
        new = [v for v in dict.fromkeys(values) if v and v not in self.values]
        if not new:
            return
        self.values.extend(new)
        for listener in self.listeners:
            listener(new)


def mask_derived_text(result: RunResult, redactor: Redactor) -> None:
    """Mask derived secret values in the fields of *result* that carry a step's values (BE-0472).

    Limited to the value-bearing text — the failure, each outcome's reason and produced value, each
    assertion's detail and reason, and each alert's label (a banner's label is the notification's
    own text, which the app can fill with a derived value) — rather than every string: a derived value can be one
    character, and masking it in an artifact path or a device name would break the report's links.
    """
    if not redactor.active:
        return
    if result.failure is not None:
        result.failure = redactor.redact_derived(result.failure)
    result.expect_results = [_mask_assertion(a, redactor) for a in result.expect_results]
    result.expect_alerts = [_mask_alert(a, redactor) for a in result.expect_alerts]
    for outcome in (*result.steps, *result.before_outcomes, *result.after_outcomes):
        outcome.reason = redactor.redact_derived(outcome.reason)
        if outcome.generated is not None:
            outcome.generated = redactor.redact_derived(outcome.generated)
        outcome.assertion_results = [
            _mask_assertion(a, redactor) for a in outcome.assertion_results
        ]
        outcome.alerts = [_mask_alert(a, redactor) for a in outcome.alerts]


def _mask_assertion(a: AssertionResult, redactor: Redactor) -> AssertionResult:
    return replace(
        a, detail=redactor.redact_derived(a.detail), reason=redactor.redact_derived(a.reason)
    )


def _mask_alert(a: AlertEvent, redactor: Redactor) -> AlertEvent:
    return replace(a, label=redactor.redact_derived(a.label))
