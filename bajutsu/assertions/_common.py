"""Shared leaf for the assertion evaluators.

Holds the `AssertionResult` every evaluator returns plus the tiny helpers used across the
package (regex caching, selector formatting, single-element resolution). Kept dependency-free
so the per-kind modules (`network`, `visual`, `schema`, `evaluate`) can all import it without
forming a cycle.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bajutsu.drivers import base
from bajutsu.scenario import Selector

if TYPE_CHECKING:
    from bajutsu.assertions.visual import VisualEvidence


@functools.lru_cache(maxsize=128)
def _compile(pattern: str) -> re.Pattern[str]:
    """Cached re.compile — avoids recompiling the same pattern on every poll iteration."""
    return re.compile(pattern)


@dataclass(frozen=True)
class AssertionResult:
    """The outcome of one assertion check, carried into the manifest/report."""

    ok: bool
    kind: str
    detail: str  # what was checked (for the report)
    reason: str = ""  # failure reason (empty when ok)
    visual: VisualEvidence | None = None  # set only for `visual` assertions


def sel_str(sel: Selector) -> str:
    """Render a selector as `key=value` pairs for report/progress detail.

    Shared by the assertion report and the wait progress line so the two never drift.
    """
    return ", ".join(f"{k}={v!r}" for k, v in sel.as_selector().items())


def _resolve_one(elements: list[base.Element], sel: Selector) -> tuple[base.Element | None, str]:
    """Resolve a single element. On failure returns (None, reason).

    Ambiguous / not-found are treated as assertion failures.
    """
    try:
        return base.resolve_unique(elements, sel.as_selector()), ""
    except base.SelectorError as e:
        return None, str(e)
