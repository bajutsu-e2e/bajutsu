"""The button a `handleSystemAlert` step tapped, and what chose it (BE-0445)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SystemAlertTap:
    """The button a `handleSystemAlert` step itself tapped, recorded on its outcome for the report.

    `rule` says how the step chose it — the author's own `sel`, the label table for the run's
    language, or a position rule such as "button 2 of 2" — so a run under a language the label table
    does not cover still shows what was tapped and why. Evidence only; nothing on the verdict path
    reads it.
    """

    label: str = ""
    rule: str = ""
