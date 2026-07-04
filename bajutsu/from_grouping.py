"""Display grouping for the `from:` provenance field (BE-0044).

`from:` records the natural-language phrase a construct was recorded from. When one utterance
produces several steps they carry the *same* `from:`, so the timeline and the report collapse a run
of identical consecutive values into one labeled group (no span syntax — grouping is emergent). This
is pure presentation; `run` never reads `from:`, so nothing here touches the deterministic gate.
"""

from __future__ import annotations


def grouped_provenance(froms: list[str | None]) -> list[str | None]:
    """For each step, the `from:` to display — the value when it starts a new run, else None.

    A run of identical consecutive `from:` strings is labeled once (on the first); an absent value
    (None or empty) never displays and ends the run, so an equal value after a gap is a new group.
    """
    # Pair each value with its predecessor (None before the first); display it only when it opens a
    # new run — truthy and different from the previous value.
    return [f if f and f != prev else None for prev, f in zip([None, *froms], froms, strict=False)]
