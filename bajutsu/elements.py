"""Platform-neutral predicates and geometry over a normalized element tree.

Pure helpers the deterministic core shares across backends and paths — assertions, the crawl,
the runner pipeline, and record — computed from a ``list[base.Element]`` alone (no browser, no
Simulator). They live here, not in a periphery module, so the core does not depend on the record
/ AI paths to read an element tree (BE-0112).
"""

from __future__ import annotations

from bajutsu.drivers import base


def screen_size_from_elements(elements: list[base.Element]) -> tuple[float, float]:
    """Max frame width/height across all elements (the screen bounds)."""
    w = max((el["frame"][0] + el["frame"][2] for el in elements), default=0.0)
    h = max((el["frame"][1] + el["frame"][3] for el in elements), default=0.0)
    return (w, h)


def shows_app_ui(elements: list[base.Element]) -> bool:
    """Whether the tree shows the app's own UI (rather than being collapsed under a system overlay).

    A SpringBoard alert collapses the app's tree to a bare window; a live app screen
    has actionable content. "Actionable" = any non-application element carrying an `id` OR a
    `label`, so apps WITHOUT accessibility identifiers (label/coordinate-driven, e.g. the
    showcase `-noax` variants) are not mistaken for a blocked screen — the bug that made the
    guard fire every turn.
    """
    return any(
        (el.get("identifier") or el.get("label")) and "application" not in (el.get("traits") or [])
        for el in elements
    )
