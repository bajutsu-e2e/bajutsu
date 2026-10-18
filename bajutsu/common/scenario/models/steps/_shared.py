"""The step's non-action modifier names, and the rebuild that closes its forward references."""

from __future__ import annotations

# The action field names, derived from the model so a new action is declared in exactly one
# place — adding a `Step` field — instead of also appending to a parallel hand-maintained tuple
# (a per-action merge-conflict point). `_MODIFIERS` are the non-action fields.
_MODIFIERS = (
    "capture",
    "extract",
    "name",
    "from_",
    "target",
    "report_group",
    "report_group_id",
    "skip_if_unsupported",
    "skip_unit_id",
)
# The modifiers expansion carries onto the steps a `use:` or a target group produces instead of
# dropping them (BE-0446, BE-0457), so both blocks accept them.
_CARRIED_MODIFIERS = ("target", "skip_if_unsupported")
