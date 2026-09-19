"""The step's non-action modifier names, and the rebuild that closes its forward references."""

from __future__ import annotations

# The action field names, derived from the model so a new action is declared in exactly one
# place — adding a `Step` field — instead of also appending to a parallel hand-maintained tuple
# (a per-action merge-conflict point). `_MODIFIERS` are the non-action fields.
_MODIFIERS = ("capture", "extract", "name", "from_", "target")
