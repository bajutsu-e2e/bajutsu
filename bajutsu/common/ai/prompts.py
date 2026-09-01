"""Shared fragments for the AI-path system prompts and element rendering (BE-0246 Unit 5).

The record / enrich / triage / crawl prompts all speak to a model about the same two things:
the prime-directive-1 boundary (the model advises, it never decides pass/fail) and the current
screen's element tree. Both were hand-copied across those files with small, drifting variations.
This module holds the single source for each so a new AI path composes them instead of retyping.
None of this reaches the deterministic `run` / CI verdict.
"""

from collections.abc import Iterable, Mapping
from typing import Any

# The prime-directive-1 boundary, previously hand-copied (and slightly reworded) across the triage
# and crawl system prompts. Each prompt keeps its own role clause and composes this shared sentence.
NEVER_JUDGE_BOUNDARY = "You never decide pass/fail and never judge results."


def render_elements(elements: Iterable[Mapping[str, Any]], *, compact: bool) -> list[str]:
    """Render an accessibility element tree as model-readable ``- …`` bullet lines.

    Args:
        elements: The screen's elements. Read structurally (``id`` / ``label`` / ``value`` /
            ``traits``), so any mapping shape works, not just the driver ``Element`` TypedDict.
        compact: Emit only the non-empty addressing fields (the record / crawl view) when True;
            emit all four fields, empties included, when False (the enrich / triage view).

    Returns:
        One bullet line per rendered element, with no leading indentation and no empty-state
        caption — callers indent and supply their own "nothing here" line. The application root
        and elements carrying no addressing field are skipped. Field order (id, label, value,
        traits), `repr` quoting, and traits formatting are unified across every call site.
    """
    lines: list[str] = []
    for element in elements:
        identifier = element.get("identifier")
        label = element.get("label")
        value = element.get("value")
        traits = element.get("traits") or []
        if "application" in traits:
            continue  # the app root is never an actionable target
        if not (identifier or label or value or traits):
            continue  # nothing to address it by
        if compact:
            fields = [
                part
                for part in (
                    f"id={identifier!r}" if identifier else None,
                    f"label={label!r}" if label else None,
                    f"value={value!r}" if value else None,
                    f"traits={traits}" if traits else None,
                )
                if part is not None
            ]
        else:
            fields = [
                f"id={(identifier or '')!r}",
                f"label={(label or '')!r}",
                f"value={(value or '')!r}",
                f"traits={traits}",
            ]
        lines.append("- " + " ".join(fields))
    return lines
