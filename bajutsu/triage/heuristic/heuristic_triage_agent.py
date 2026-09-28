"""The rule-based triage agent — deterministic, and the only one that needs no model."""

from __future__ import annotations

from bajutsu.common.drivers.base import AMBIGUOUS_MATCH_MARKER, LEGACY_AMBIGUOUS_MATCH_MARKER

from ._functions import _close, _ids, fix_summary
from .fix import Fix
from .triage import Triage
from .triage_context import TriageContext

_ACT_TARGETS = (
    "tap",
    "double_tap",
    "long_press",
    "type",
    "swipe",
    "pinch",
    "rotate",
    "drag",
    "scroll",
)


class HeuristicTriageAgent:
    """A deterministic, rule-based triage (no AI).

    Categorizes the failure by its shape and points at the likely fix — including a "did you mean"
    when the target id is absent but a similar id is on screen (the classic self-heal: an id was
    renamed).
    """

    def triage(self, context: TriageContext) -> Triage:
        fs = context.failed_step
        absent = bool(
            context.target_id
            and context.elements
            and context.target_id not in _ids(context.elements)
        )
        hints = []
        fix: Fix | None = None
        if absent and context.target_id:
            close = _close(context.target_id, context.elements)
            hints.append(
                f"`{context.target_id}` is not on the captured screen"
                + (
                    f" — did you mean {', '.join('`' + c + '`' for c in close)}?"
                    if close
                    else " (its id may have changed, or the screen differs from expected)."
                )
            )
            if close:  # a confident rename — the deterministic, whole-token self-heal
                fix = Fix(
                    "renameId",
                    fix_summary("renameId", context.target_id, close[0]),
                    context.target_id,
                    close[0],
                )

        if fs is not None and fs.action == "wait":
            sugg = [
                *hints,
                "Raise the wait timeout, or check the awaited element/condition is reachable.",
            ]
            return Triage(
                "A wait condition was not met before its timeout.", "timing", sugg, fix=fix
            )

        if fs is not None and fs.action in _ACT_TARGETS:
            # `LEGACY_AMBIGUOUS_MATCH_MARKER` catches a manifest recorded before the message was
            # translated to English — triage reads stored runs, and `assemble` accepts any manifest
            # with no `schemaVersion` gate, so an old run's `reason` can still carry the old wording.
            is_ambiguous = (
                AMBIGUOUS_MATCH_MARKER in fs.reason or LEGACY_AMBIGUOUS_MATCH_MARKER in fs.reason
            )
            if is_ambiguous and context.target_id:
                sugg = [
                    f"`{context.target_id}` matched multiple elements — add `within` or `index` to disambiguate."
                ]
            else:
                sugg = hints or [
                    "Verify the selector resolves to exactly one element (see the element tree)."
                ]
            return Triage(
                f"The `{fs.action}` step could not resolve or act on its target.",
                "selector",
                sugg,
                fix=fix,
            )

        if context.failed_expectations:
            sugg = [
                *hints,
                "Compare each failed expectation below with the screen state at the end of the run.",
            ]
            return Triage("An expectation did not hold.", "assertion", sugg)

        return Triage(
            context.failure or "The scenario failed.",
            "unknown",
            ["Inspect the run with `bajutsu trace` and the captured screenshots / logs."],
        )
